from __future__ import annotations

import os
import base64
import json
from uuid import uuid4
from functools import lru_cache
from pathlib import Path
from typing import Any
from urllib.parse import quote

import httpx
from dotenv import load_dotenv
from fastapi import HTTPException, status

load_dotenv(Path(__file__).resolve().parents[1] / ".env", override=False)


class SupabaseService:
    def __init__(self, url: str, api_key: str) -> None:
        self.url = url.rstrip("/")
        self.api_key = api_key
        self.is_legacy_jwt_key = len(api_key.split(".")) == 3

    def verify_access_token(self, token: str) -> dict[str, Any]:
        try:
            response = httpx.get(
                f"{self.url}/auth/v1/user",
                headers={"apikey": self.api_key, "Authorization": f"Bearer {token}"},
                timeout=10,
            )
        except httpx.HTTPError as error:
            raise HTTPException(status_code=503, detail="Authentication service unavailable") from error

        if response.status_code != 200:
            raise HTTPException(status_code=401, detail="Invalid or expired token")

        user = response.json()
        if not isinstance(user, dict) or not user.get("id"):
            raise HTTPException(status_code=401, detail="Invalid or expired token")
        return user

    def upload_listing_image(self, seller_id: str, content: bytes, content_type: str) -> str:
        extension = {"image/jpeg": "jpg", "image/png": "png", "image/webp": "webp"}[content_type]
        path = f"{seller_id}/{uuid4()}.{extension}"
        headers = {
            "apikey": self.api_key,
            "Content-Type": content_type,
            "x-upsert": "false",
        }
        if self.is_legacy_jwt_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        try:
            response = httpx.post(
                f"{self.url}/storage/v1/object/book-images/{path}",
                content=content,
                headers=headers,
                timeout=30,
            )
        except httpx.HTTPError as error:
            raise HTTPException(status_code=503, detail="Image storage service unavailable") from error

        if response.is_error:
            raise HTTPException(status_code=502, detail="Book photo could not be uploaded")

        return f"{self.url}/storage/v1/object/public/book-images/{quote(path, safe='/')}"

    def rest(
        self,
        method: str,
        table: str,
        *,
        params: dict[str, str] | None = None,
        payload: dict[str, Any] | None = None,
        prefer: str | None = None,
    ) -> httpx.Response:
        headers = {
            "apikey": self.api_key,
            "Accept": "application/json",
        }
        if self.is_legacy_jwt_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        if payload is not None:
            headers["Content-Type"] = "application/json"
        if prefer:
            headers["Prefer"] = prefer

        try:
            response = httpx.request(
                method,
                f"{self.url}/rest/v1/{table}",
                params=params,
                json=payload,
                headers=headers,
                timeout=10,
            )
        except httpx.HTTPError as error:
            raise HTTPException(status_code=503, detail="Database service unavailable") from error

        if response.is_error:
            try:
                error_payload = response.json()
            except ValueError:
                error_payload = {}
            message = error_payload.get("message", "") if isinstance(error_payload, dict) else ""
            domain_errors = {
                "listing_not_found": (404, "Listing not found"),
                "request_not_found": (404, "Request not found"),
                "transaction_not_found": (404, "Transaction not found"),
                "own_listing": (403, "You cannot request your own listing"),
                "buyer_only_cancel": (403, "Only the buyer can cancel a request"),
                "seller_only_decision": (403, "Only the seller can accept or reject a request"),
                "transaction_participant_only": (403, "You are not authorized to complete this transaction"),
                "listing_unavailable": (400, "Inactive, reserved, or sold listings cannot receive this action"),
                "request_not_pending": (409, "Only pending requests can be updated"),
                "duplicate_pending_request": (409, "You already have a pending request for this listing"),
                "transaction_not_arranging": (409, "Only arranging transactions can be completed"),
                "unsupported_request_status": (400, "Unsupported request status update"),
            }
            if message in domain_errors:
                code, detail = domain_errors[message]
                raise HTTPException(status_code=code, detail=detail)
            raise HTTPException(status_code=502, detail="Database request failed")
        return response

    def rpc(self, function: str, payload: dict[str, Any]) -> Any:
        response = self.rest("POST", f"rpc/{function}", payload=payload)
        return response.json()

    def list_listings(
        self,
        *,
        query: str | None,
        listing_type: str | None,
        condition: str | None,
        page: int,
        page_size: int,
    ) -> tuple[list[dict[str, Any]], int]:
        params = {
            "select": "*,profiles(display_name)",
            "status": "eq.active",
            "order": "created_at.desc",
            "offset": str((page - 1) * page_size),
            "limit": str(page_size),
        }
        if query and query.strip():
            term = query.strip().replace("%", "")
            params["or"] = f"(title.ilike.*{term}*,author.ilike.*{term}*)"
        if listing_type:
            params["listing_type"] = f"eq.{listing_type}"
        if condition:
            params["condition"] = f"eq.{condition}"

        response = self.rest("GET", "listings", params=params, prefer="count=exact")
        content_range = response.headers.get("content-range", "*/0")
        try:
            total = int(content_range.rsplit("/", 1)[1])
        except (IndexError, ValueError):
            total = len(response.json())
        return [self._listing_view(row) for row in response.json()], total

    def get_listing(self, listing_id: str, seller_id: str | None = None) -> dict[str, Any]:
        params = {"select": "*,profiles(display_name)", "id": f"eq.{listing_id}", "limit": "1"}
        if seller_id is None:
            params["status"] = "eq.active"
        else:
            params["seller_id"] = f"eq.{seller_id}"
        response = self.rest(
            "GET",
            "listings",
            params=params,
        )
        rows = response.json()
        if not rows:
            raise HTTPException(status_code=404, detail="Listing not found")
        return self._listing_view(rows[0])

    def create_listing(self, payload: dict[str, Any], seller_id: str) -> dict[str, Any]:
        response = self.rest(
            "POST",
            "listings",
            params={"select": "*,profiles(display_name)"},
            payload={**payload, "seller_id": seller_id},
            prefer="return=representation",
        )
        rows = response.json()
        return self._listing_view(rows[0])

    def update_listing(self, listing_id: str, seller_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        existing = self._get_owned_listing(listing_id, seller_id)
        if not payload:
            return existing
        response = self.rest(
            "PATCH",
            "listings",
            params={"select": "*,profiles(display_name)", "id": f"eq.{listing_id}", "seller_id": f"eq.{seller_id}"},
            payload=payload,
            prefer="return=representation",
        )
        rows = response.json()
        if not rows:
            raise HTTPException(status_code=404, detail="Listing not found")
        return self._listing_view(rows[0])

    def deactivate_listing(self, listing_id: str, seller_id: str) -> None:
        self._get_owned_listing(listing_id, seller_id)
        self.rest(
            "PATCH",
            "listings",
            params={"id": f"eq.{listing_id}", "seller_id": f"eq.{seller_id}"},
            payload={"status": "inactive"},
            prefer="return=minimal",
        )

    def my_listings(self, seller_id: str) -> list[dict[str, Any]]:
        response = self.rest(
            "GET",
            "listings",
            params={"select": "*,profiles(display_name)", "seller_id": f"eq.{seller_id}", "order": "created_at.desc"},
        )
        return [self._listing_view(row) for row in response.json()]

    def create_request(self, listing_id: str, buyer_id: str, message: str | None) -> dict[str, Any]:
        result = self.rpc(
            "create_transaction_request",
            {"p_listing_id": listing_id, "p_buyer_id": buyer_id, "p_message": message or ""},
        )
        return result

    def update_request(self, request_id: str, actor_id: str, next_status: str) -> dict[str, Any]:
        return self.rpc(
            "update_transaction_request",
            {"p_request_id": request_id, "p_actor_id": actor_id, "p_next_status": next_status},
        )

    def my_requests(self, user_id: str) -> list[dict[str, Any]]:
        buyer_response = self.rest(
            "GET",
            "transaction_requests",
            params={
                "select": "*,listings(id,title,seller_id)",
                "buyer_id": f"eq.{user_id}",
                "order": "created_at.desc",
            },
        )
        seller_response = self.rest(
            "GET",
            "transaction_requests",
            params={
                "select": "*,listings!inner(id,title,seller_id)",
                "listings.seller_id": f"eq.{user_id}",
                "order": "created_at.desc",
            },
        )
        by_id = {row["id"]: row for row in buyer_response.json()}
        by_id.update({row["id"]: row for row in seller_response.json()})
        return [self._request_view(row) for row in by_id.values()]

    def my_transactions(self, user_id: str) -> list[dict[str, Any]]:
        response = self.rest(
            "GET",
            "transactions",
            params={
                "select": "*,listings(title)",
                "or": f"(buyer_id.eq.{user_id},seller_id.eq.{user_id})",
                "order": "created_at.desc",
            },
        )
        return [self._transaction_view(row) for row in response.json()]

    def complete_transaction(self, transaction_id: str, actor_id: str) -> dict[str, Any]:
        return self.rpc(
            "complete_book_transaction",
            {"p_transaction_id": transaction_id, "p_actor_id": actor_id},
        )

    def create_report(self, payload: dict[str, Any], reporter_id: str) -> dict[str, Any]:
        response = self.rest(
            "POST",
            "reports",
            params={"select": "*"},
            payload={**payload, "reporter_id": reporter_id},
            prefer="return=representation",
        )
        rows = response.json()
        if not rows:
            raise HTTPException(status_code=502, detail="Report was not saved")
        return rows[0]

    def my_reports(self, reporter_id: str, is_moderator: bool) -> list[dict[str, Any]]:
        params: dict[str, str] = {"select": "*", "order": "created_at.desc"}
        if not is_moderator:
            params["reporter_id"] = f"eq.{reporter_id}"
        return self.rest("GET", "reports", params=params).json()

    def update_report(self, report_id: str, next_status: str) -> dict[str, Any]:
        response = self.rest(
            "PATCH",
            "reports",
            params={"id": f"eq.{report_id}", "select": "*"},
            payload={"status": next_status},
            prefer="return=representation",
        )
        rows = response.json()
        if not rows:
            raise HTTPException(status_code=404, detail="Report not found")
        return rows[0]

    def _get_owned_listing(self, listing_id: str, seller_id: str) -> dict[str, Any]:
        response = self.rest(
            "GET",
            "listings",
            params={
                "select": "*,profiles(display_name)",
                "id": f"eq.{listing_id}",
                "seller_id": f"eq.{seller_id}",
                "limit": "1",
            },
        )
        rows = response.json()
        if not rows:
            raise HTTPException(status_code=404, detail="Listing not found")
        return self._listing_view(rows[0])

    @staticmethod
    def _listing_view(row: dict[str, Any]) -> dict[str, Any]:
        profile = row.pop("profiles", None) or {}
        row["seller_name"] = profile.get("display_name") or "BookNook user"
        row["images"] = row.get("images") or []
        return row

    @staticmethod
    def _request_view(row: dict[str, Any]) -> dict[str, Any]:
        listing = row.pop("listings", None) or {}
        row["seller_id"] = listing.get("seller_id")
        row["listing_title"] = listing.get("title")
        return row

    @staticmethod
    def _transaction_view(row: dict[str, Any]) -> dict[str, Any]:
        listing = row.pop("listings", None) or {}
        row["listing_title"] = listing.get("title")
        return row


@lru_cache(maxsize=1)
def get_supabase_service() -> SupabaseService | None:
    storage = os.getenv("BOOKNOOK_STORAGE", "memory").strip().lower()
    if storage == "memory":
        return None
    if storage != "supabase":
        raise RuntimeError("BOOKNOOK_STORAGE must be either 'memory' or 'supabase'")

    url = os.getenv("SUPABASE_URL", "").strip()
    api_key = os.getenv("SUPABASE_SECRET_KEY", "").strip()
    if not all((url, api_key)):
        raise RuntimeError("Supabase storage requires SUPABASE_URL and SUPABASE_SECRET_KEY")
    if not _is_privileged_supabase_key(api_key):
        raise RuntimeError(
            "SUPABASE_SECRET_KEY must be an sb_secret_ key or a legacy service_role key; "
            "publishable/anon keys cannot be used for backend database writes"
        )
    return SupabaseService(url, api_key)


def _is_privileged_supabase_key(api_key: str) -> bool:
    if api_key.startswith("sb_secret_"):
        return True

    parts = api_key.split(".")
    if len(parts) != 3:
        return False

    try:
        payload_segment = parts[1]
        payload_segment += "=" * (-len(payload_segment) % 4)
        claims = json.loads(base64.urlsafe_b64decode(payload_segment))
    except (ValueError, json.JSONDecodeError):
        return False

    return isinstance(claims, dict) and claims.get("role") == "service_role"