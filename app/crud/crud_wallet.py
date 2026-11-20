"""Wallet balances, e-wallet top-ups, and the admin overview.

``get_wallet_store()`` returns a Supabase-backed store in persistent mode and an
in-memory store in demo/test mode. Both expose the same methods.

Balances only change through ``approve_topup``: a moderator checks the receipt
against the platform's e-wallet account before crediting the user.
"""

from __future__ import annotations

import base64
import os
from datetime import datetime, timezone
from threading import Lock
from typing import Any
from urllib.parse import quote
from uuid import uuid4

import httpx
from fastapi import HTTPException

from core.supabase import get_supabase_service
from crud.crud_item import LISTINGS, REPORTS, REQUESTS, TRANSACTIONS
from crud.crud_notifications import MEMORY_NOTIFICATION_STORE
from crud.crud_user import MEMORY_AUTH_STORE

RECEIPT_BUCKET = "wallet-receipts"
RECEIPT_URL_TTL_SECONDS = 10 * 60
DEFAULT_EWALLET_NUMBER = "01109261212"
_EXTENSIONS = {"image/jpeg": "jpg", "image/png": "png", "image/webp": "webp"}
INSUFFICIENT_FUNDS = "Insufficient wallet balance"


def ewallet_recipient_number() -> str:
    """The platform's e-wallet number users transfer money to (configurable per deployment)."""
    return os.getenv("WALLET_EWALLET_NUMBER", DEFAULT_EWALLET_NUMBER).strip() or DEFAULT_EWALLET_NUMBER


def charging_options() -> list[dict[str, str]]:
    return [{"method": "e_wallet", "recipient_number": ewallet_recipient_number()}]


class SupabaseWalletStore:
    def __init__(self, service: Any) -> None:
        self.service = service

    def get_balance(self, user_id: str) -> int:
        rows = self.service.rest(
            "GET", "wallets", params={"select": "balance_egp", "user_id": f"eq.{user_id}", "limit": "1"}
        ).json()
        return int(rows[0]["balance_egp"]) if rows else 0

    def summary(self, user_id: str) -> dict[str, Any]:
        return self.service.rpc("wallet_summary", {"p_user_id": user_id})

    def count_pending(self, user_id: str) -> int:
        rows = self.service.rest(
            "GET", "wallet_topups", params={"select": "id", "user_id": f"eq.{user_id}", "status": "eq.pending"}
        ).json()
        return len(rows)

    def save_receipt(self, user_id: str, content: bytes, content_type: str) -> str:
        path = f"{user_id}/{uuid4()}.{_EXTENSIONS[content_type]}"
        response = self._storage("POST", f"object/{RECEIPT_BUCKET}/{quote(path, safe='/')}", content=content,
                                 headers={"Content-Type": content_type, "x-upsert": "false"})
        if response.is_error:
            raise HTTPException(status_code=502, detail="Receipt could not be uploaded")
        return path

    def create_topup(self, row: dict[str, Any]) -> dict[str, Any]:
        rows = self.service.rest(
            "POST", "wallet_topups", params={"select": "*"}, payload=row, prefer="return=representation"
        ).json()
        if not rows:
            raise HTTPException(status_code=502, detail="Top-up was not saved")
        return rows[0]

    def list_topups(self, *, user_id: str | None = None, status: str | None = None) -> list[dict[str, Any]]:
        params = {
            "select": "*,owner:users!wallet_topups_user_id_fkey(email)",
            "order": "created_at.asc" if status == "pending" else "created_at.desc",
            "limit": "200",
        }
        if user_id:
            params["user_id"] = f"eq.{user_id}"
        if status:
            params["status"] = f"eq.{status}"
        rows = self.service.rest("GET", "wallet_topups", params=params).json()
        for row in rows:
            row["user_email"] = (row.pop("owner", None) or {}).get("email")
        return rows

    def receipt_urls(self, paths: list[str]) -> dict[str, str]:
        if not paths:
            return {}
        response = self._storage("POST", f"object/sign/{RECEIPT_BUCKET}",
                                 json={"expiresIn": RECEIPT_URL_TTL_SECONDS, "paths": paths})
        if response.is_error:
            return {}
        urls: dict[str, str] = {}
        for item in response.json():
            signed = item.get("signedURL") if isinstance(item, dict) else None
            if signed:
                urls[item["path"]] = f"{self.service.url}/storage/v1{signed}"
        return urls

    def approve_topup(self, topup_id: str, reviewer_id: str) -> dict[str, Any]:
        return self.service.rpc("approve_wallet_topup", {"p_topup_id": topup_id, "p_reviewer_id": reviewer_id})

    def reject_topup(self, topup_id: str, reviewer_id: str, note: str | None) -> dict[str, Any]:
        return self.service.rpc(
            "reject_wallet_topup", {"p_topup_id": topup_id, "p_reviewer_id": reviewer_id, "p_note": note or ""}
        )

    def overview(self) -> dict[str, Any]:
        return self.service.rpc("admin_overview", {})

    def _storage(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        headers = {"apikey": self.service.api_key, **kwargs.pop("headers", {})}
        if self.service.is_legacy_jwt_key:
            headers["Authorization"] = f"Bearer {self.service.api_key}"
        try:
            return httpx.request(method, f"{self.service.url}/storage/v1/{path}", headers=headers, timeout=30, **kwargs)
        except httpx.HTTPError as error:
            raise HTTPException(status_code=503, detail="Receipt storage service unavailable") from error


class MemoryWalletStore:
    """Demo/test-only store. Data (including receipt images) is lost when the process restarts."""

    def __init__(self) -> None:
        self._lock = Lock()
        self.reset()

    def reset(self) -> None:
        self.balances: dict[str, int] = {}
        self.topups: dict[str, dict[str, Any]] = {}
        self.receipts: dict[str, tuple[bytes, str]] = {}
        # Ledger: user_id None rows are platform fee revenue (mirrors public.wallet_entries).
        self.entries: list[dict[str, Any]] = []

    def get_balance(self, user_id: str) -> int:
        return self.balances.get(user_id, 0)

    def adjust(self, user_id: str, delta: int, kind: str, *, request_id: str | None = None,
               transaction_id: str | None = None, topup_id: str | None = None) -> None:
        """Changes a balance and records it; refuses to go negative (callers check before mutating)."""
        if delta == 0:
            return
        balance = self.balances.get(user_id, 0) + delta
        if balance < 0:
            raise HTTPException(status_code=402, detail=INSUFFICIENT_FUNDS)
        self.balances[user_id] = balance
        self._entry(user_id, delta, kind, request_id, transaction_id, topup_id)

    def record_fee(self, amount: int, transaction_id: str) -> None:
        if amount > 0:
            self._entry(None, amount, "fee", None, transaction_id, None)

    def summary(self, user_id: str) -> dict[str, Any]:
        held = sum(int(r.get("held_egp") or 0) for r in REQUESTS if r["buyer_id"] == user_id and r["status"] == "pending")
        for t in TRANSACTIONS:
            if t["status"] == "arranging":
                held += int(t.get("buyer_held_egp") or 0) if t["buyer_id"] == user_id else 0
                held += int(t.get("seller_held_egp") or 0) if t["seller_id"] == user_id else 0
        entries = [{**e, "listing_title": _entry_title(e)} for e in reversed(self.entries) if e["user_id"] == user_id][:50]
        return {"balance_egp": self.get_balance(user_id), "held_egp": held, "entries": entries}

    def _entry(self, user_id: str | None, amount: int, kind: str, request_id: str | None,
               transaction_id: str | None, topup_id: str | None) -> None:
        self.entries.append({
            "id": str(uuid4()), "user_id": user_id, "amount_egp": amount, "kind": kind, "request_id": request_id,
            "transaction_id": transaction_id, "topup_id": topup_id, "created_at": _now(),
        })

    def count_pending(self, user_id: str) -> int:
        return sum(1 for t in self.topups.values() if t["user_id"] == user_id and t["status"] == "pending")

    def save_receipt(self, user_id: str, content: bytes, content_type: str) -> str:
        path = f"{user_id}/{uuid4()}.{_EXTENSIONS[content_type]}"
        self.receipts[path] = (content, content_type)
        return path

    def create_topup(self, row: dict[str, Any]) -> dict[str, Any]:
        now = _now()
        topup = {
            "id": str(uuid4()), **row, "status": "pending", "review_note": None, "reviewed_by": None,
            "reviewed_at": None, "created_at": now, "updated_at": now,
        }
        with self._lock:
            self.topups[topup["id"]] = topup
        return dict(topup)

    def list_topups(self, *, user_id: str | None = None, status: str | None = None) -> list[dict[str, Any]]:
        rows = [
            {**t, "user_email": (MEMORY_AUTH_STORE.get_user(t["user_id"]) or {}).get("email")}
            for t in self.topups.values()
            if (user_id is None or t["user_id"] == user_id) and (status is None or t["status"] == status)
        ]
        rows.sort(key=lambda t: t["created_at"], reverse=status != "pending")
        return rows

    def receipt_urls(self, paths: list[str]) -> dict[str, str]:
        urls = {}
        for path in paths:
            if path in self.receipts:
                content, content_type = self.receipts[path]
                urls[path] = f"data:{content_type};base64,{base64.b64encode(content).decode('ascii')}"
        return urls

    def approve_topup(self, topup_id: str, reviewer_id: str) -> dict[str, Any]:
        with self._lock:
            topup = self._pending(topup_id)
            self.adjust(topup["user_id"], topup["amount_egp"], "topup", topup_id=topup_id)
            reviewed = self._review(topup, "approved", reviewer_id, None)
            _notify_topup(reviewed)
            return reviewed

    def reject_topup(self, topup_id: str, reviewer_id: str, note: str | None) -> dict[str, Any]:
        with self._lock:
            reviewed = self._review(self._pending(topup_id), "rejected", reviewer_id, note or None)
            _notify_topup(reviewed)
            return reviewed

    def overview(self) -> dict[str, Any]:
        pending = [t for t in self.topups.values() if t["status"] == "pending"]
        return {
            "users_total": len(MEMORY_AUTH_STORE.users),
            "listings_active": sum(1 for item in LISTINGS if item["status"] == "active"),
            "listings_total": len(LISTINGS),
            "requests_pending": sum(1 for item in REQUESTS if item["status"] == "pending"),
            "transactions_arranging": sum(1 for item in TRANSACTIONS if item["status"] == "arranging"),
            "transactions_completed": sum(1 for item in TRANSACTIONS if item["status"] == "completed"),
            "reports_open": sum(1 for item in REPORTS if item["status"] in {"open", "reviewing"}),
            "topups_pending": len(pending),
            "topups_pending_egp": sum(t["amount_egp"] for t in pending),
            "wallet_balance_total_egp": sum(self.balances.values()),
            "held_total_egp": sum(int(r.get("held_egp") or 0) for r in REQUESTS if r["status"] == "pending")
            + sum(int(t.get("buyer_held_egp") or 0) + int(t.get("seller_held_egp") or 0)
                  for t in TRANSACTIONS if t["status"] == "arranging"),
            "platform_revenue_egp": sum(e["amount_egp"] for e in self.entries if e["kind"] == "fee"),
        }

    def _pending(self, topup_id: str) -> dict[str, Any]:
        topup = self.topups.get(topup_id)
        if topup is None:
            raise HTTPException(status_code=404, detail="Top-up not found")
        if topup["status"] != "pending":
            raise HTTPException(status_code=409, detail="Only pending top-ups can be reviewed")
        return topup

    @staticmethod
    def _review(topup: dict[str, Any], status: str, reviewer_id: str, note: str | None) -> dict[str, Any]:
        now = _now()
        topup.update(status=status, review_note=note, reviewed_by=reviewer_id, reviewed_at=now, updated_at=now)
        return dict(topup)


def _notify_topup(topup: dict[str, Any]) -> None:
    """Memory-mode mirror of the migration 011 top-up trigger."""
    MEMORY_NOTIFICATION_STORE.emit(
        topup["user_id"], f"topup_{topup['status']}",
        {"amount_egp": topup["amount_egp"], "note": (topup.get("review_note") or "")[:200] or None},
        topup_id=topup["id"],
    )


def _entry_title(entry: dict[str, Any]) -> str | None:
    """Title of the book a ledger entry relates to (via its request or transaction)."""
    listing_id = next((r["listing_id"] for r in REQUESTS if r["id"] == entry.get("request_id")), None) \
        or next((t["listing_id"] for t in TRANSACTIONS if t["id"] == entry.get("transaction_id")), None)
    return next((str(item["title"]) for item in LISTINGS if item["id"] == listing_id), None)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


MEMORY_WALLET_STORE = MemoryWalletStore()


def get_wallet_store() -> SupabaseWalletStore | MemoryWalletStore:
    supabase = get_supabase_service()
    return SupabaseWalletStore(supabase) if supabase is not None else MEMORY_WALLET_STORE
