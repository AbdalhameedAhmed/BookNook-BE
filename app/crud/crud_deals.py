"""Requests, handovers, and chat for wallet-paid sales and exchanges.

``get_deal_store()`` returns a Supabase-backed store (thin wrappers around the
atomic SQL functions in migration 008) or an in-memory store that mirrors the
same rules for demo/test mode. See docs/wallet.md for the money rules.
"""

from __future__ import annotations

from datetime import datetime, timezone
from threading import RLock
from typing import Any
from uuid import UUID, uuid4

from fastapi import HTTPException

from core.supabase import get_supabase_service
from crud.crud_item import LISTINGS, REQUESTS, TRANSACTIONS, _get_listing_or_404
from crud.crud_notifications import MEMORY_NOTIFICATION_STORE, message_preview
from crud.crud_promos import MEMORY_PROMO_STORE
from crud.crud_referrals import MEMORY_REFERRAL_STORE
from crud.crud_user import MEMORY_AUTH_STORE
from crud.crud_wallet import INSUFFICIENT_FUNDS, MEMORY_WALLET_STORE

MAX_OFFERED_BOOKS = 10
FINAL_LISTING_STATUS = {"sale": "sold", "exchange": "exchanged", "free": "inactive"}


class SupabaseDealStore:
    def __init__(self, service: Any) -> None:
        self.service = service

    def create_request(self, listing_id: str, buyer_id: str, message: str | None,
                       offered_listing_ids: list[str], fee: int, promo_code: str | None = None) -> dict[str, Any]:
        _require_uuid(listing_id, "Listing not found")
        for offered in offered_listing_ids:
            _require_uuid(offered, "Offered books must be your own available listings", status=400)
        return self.service.rpc("create_transaction_request", {
            "p_listing_id": listing_id, "p_buyer_id": buyer_id, "p_message": message or "",
            "p_offered_listing_ids": offered_listing_ids, "p_fee": fee, "p_promo_code": promo_code,
        })

    def update_request(self, request_id: str, actor_id: str, next_status: str,
                       chosen_listing_id: str | None, fee: int, promo_code: str | None = None) -> dict[str, Any]:
        _require_uuid(request_id, "Request not found")
        if chosen_listing_id is not None:
            _require_uuid(chosen_listing_id, "Choose one of the offered books", status=400)
        return self.service.rpc("update_transaction_request", {
            "p_request_id": request_id, "p_actor_id": actor_id, "p_next_status": next_status,
            "p_chosen_listing_id": chosen_listing_id, "p_fee": fee,
            "p_promo_code": promo_code if next_status == "accepted" else None,
        })

    def confirm(self, transaction_id: str, actor_id: str) -> dict[str, Any]:
        _require_uuid(transaction_id, "Transaction not found")
        return self.service.rpc("complete_book_transaction", {"p_transaction_id": transaction_id, "p_actor_id": actor_id})

    def cancel(self, transaction_id: str) -> dict[str, Any]:
        _require_uuid(transaction_id, "Transaction not found")
        return self.service.rpc("cancel_book_transaction", {"p_transaction_id": transaction_id})

    def close_listing(self, listing_id: str, seller_id: str | None, next_status: str) -> dict[str, Any]:
        _require_uuid(listing_id, "Listing not found")
        return self.service.rpc("close_listing", {"p_listing_id": listing_id, "p_seller_id": seller_id, "p_status": next_status})

    def my_requests(self, user_id: str) -> list[dict[str, Any]]:
        return self.service.rpc("my_requests", {"p_user_id": user_id}) or []

    def transactions(self, user_id: str | None, status: str | None = None) -> list[dict[str, Any]]:
        return self.service.rpc("my_transactions", {"p_user_id": user_id, "p_status": status}) or []

    def get_transaction(self, transaction_id: str) -> dict[str, Any] | None:
        if not _is_uuid(transaction_id):
            return None
        rows = self.service.rest(
            "GET", "transactions",
            params={"select": "id,buyer_id,seller_id,status", "id": f"eq.{transaction_id}", "limit": "1"},
        ).json()
        return rows[0] if rows else None

    def list_messages(self, transaction_id: str) -> list[dict[str, Any]]:
        return self.service.rest("GET", "transaction_messages", params={
            "select": "id,transaction_id,sender_id,body,created_at",
            "transaction_id": f"eq.{transaction_id}", "order": "created_at.asc", "limit": "500",
        }).json()

    def add_message(self, transaction_id: str, sender_id: str, body: str) -> dict[str, Any]:
        rows = self.service.rest(
            "POST", "transaction_messages", params={"select": "id,transaction_id,sender_id,body,created_at"},
            payload={"transaction_id": transaction_id, "sender_id": sender_id, "body": body},
            prefer="return=representation",
        ).json()
        if not rows:
            raise HTTPException(status_code=502, detail="Message was not saved")
        return rows[0]


class MemoryDealStore:
    """Demo/test-only mirror of the migration 008 SQL functions. Keep the two in step."""

    def __init__(self) -> None:
        self._lock = RLock()
        self.reset()

    def reset(self) -> None:
        self.messages: list[dict[str, Any]] = []

    @property
    def wallet(self) -> Any:
        return MEMORY_WALLET_STORE

    # --- Requests ---

    def create_request(self, listing_id: str, buyer_id: str, message: str | None,
                       offered_listing_ids: list[str], fee: int, promo_code: str | None = None) -> dict[str, Any]:
        with self._lock:
            listing = _get_listing_or_404(listing_id)
            if listing["seller_id"] == buyer_id:
                raise HTTPException(status_code=403, detail="You cannot request your own listing")
            if listing["status"] != "active":
                raise HTTPException(status_code=400, detail="Inactive or sold listings cannot receive new requests")
            if any(r["listing_id"] == listing_id and r["buyer_id"] == buyer_id and r["status"] == "pending" for r in REQUESTS):
                raise HTTPException(status_code=409, detail="You already have a pending request for this listing")

            kind = str(listing["listing_type"])
            offered = list(dict.fromkeys(offered_listing_ids))
            price, request_fee = 0, 0
            if kind == "exchange":
                if not 1 <= len(offered) <= MAX_OFFERED_BOOKS:
                    raise HTTPException(status_code=400, detail="Choose at least one of your books to offer")
                if not all(_is_own_active_listing(item, buyer_id) for item in offered):
                    raise HTTPException(status_code=400, detail="Offered books must be your own available listings")
                request_fee = fee
            elif kind == "sale":
                price, request_fee = int(listing["price_egp"] or 0), fee

            promo, discount = (MEMORY_PROMO_STORE.discount_for(promo_code, request_fee, buyer_id) if promo_code else (None, 0))
            # No promo code: a free request earned by inviting a friend waives the whole fee.
            free_request = not promo_code and request_fee > 0 and MEMORY_REFERRAL_STORE.available(buyer_id) > 0
            if free_request:
                discount = request_fee
            request_fee -= discount
            held = price + request_fee
            if self.wallet.get_balance(buyer_id) < held:
                raise HTTPException(status_code=402, detail=INSUFFICIENT_FUNDS)

            now = _now()
            request = {
                "id": str(uuid4()), "listing_id": listing_id, "buyer_id": buyer_id, "seller_id": listing["seller_id"],
                "message": message or "", "status": "pending", "kind": kind, "price_egp": price,
                "fee_egp": request_fee, "fee_discount_egp": discount, "held_egp": held,
                "offers": offered if kind == "exchange" else [],
                "created_at": now, "updated_at": now,
            }
            REQUESTS.insert(0, request)
            if promo:
                MEMORY_PROMO_STORE.record(promo, buyer_id, request["id"], "buyer", discount)
            if free_request:
                MEMORY_REFERRAL_STORE.use_credit(buyer_id, request["id"], "buyer")
            self.wallet.adjust(buyer_id, -held, "hold", request_id=request["id"])
            _notify(listing["seller_id"], "request_new", listing, actor_id=buyer_id, request_id=request["id"])
            return {**_public_request(request), "free_request_used": free_request}

    def update_request(self, request_id: str, actor_id: str, next_status: str,
                       chosen_listing_id: str | None, fee: int, promo_code: str | None = None) -> dict[str, Any]:
        with self._lock:
            request = next((r for r in REQUESTS if r["id"] == request_id), None)
            if request is None:
                raise HTTPException(status_code=404, detail="Request not found")
            listing = _get_listing_or_404(request["listing_id"])

            if next_status == "cancelled":
                if request["buyer_id"] != actor_id:
                    raise HTTPException(status_code=403, detail="Only the buyer can cancel a request")
            elif next_status in {"accepted", "rejected"}:
                if listing["seller_id"] != actor_id:
                    raise HTTPException(status_code=403, detail="Only the seller can accept or reject a request")
            else:
                raise HTTPException(status_code=400, detail="Unsupported request status update")
            if request["status"] != "pending":
                raise HTTPException(status_code=409, detail="Only pending requests can be updated")

            if next_status in {"cancelled", "rejected"}:
                self._close_pending(request, next_status)
                return _public_request(request)

            if listing["status"] != "active":
                raise HTTPException(status_code=400, detail="Inactive or sold listings cannot receive new requests")

            kind = request.get("kind", listing["listing_type"])
            chosen = None
            seller_fee = 0
            if kind == "exchange":
                if not chosen_listing_id or chosen_listing_id not in request.get("offers", []):
                    raise HTTPException(status_code=400, detail="Choose one of the offered books")
                chosen = _get_listing_or_404(chosen_listing_id)
                if chosen["status"] != "active" or chosen["seller_id"] != request["buyer_id"]:
                    raise HTTPException(status_code=409, detail="The chosen book is no longer available")
                seller_fee = fee
            elif kind == "sale":
                seller_fee = min(fee, int(request.get("price_egp") or 0))
            promo, seller_discount = (MEMORY_PROMO_STORE.discount_for(promo_code, seller_fee, actor_id) if promo_code else (None, 0))
            free_request = not promo_code and seller_fee > 0 and MEMORY_REFERRAL_STORE.available(actor_id) > 0
            if free_request:
                seller_discount = seller_fee
            seller_fee -= seller_discount
            if kind == "exchange" and self.wallet.get_balance(actor_id) < seller_fee:
                raise HTTPException(status_code=402, detail=INSUFFICIENT_FUNDS)

            now = _now()
            transaction = {
                "id": str(uuid4()), "request_id": request["id"], "listing_id": listing["id"],
                "buyer_id": request["buyer_id"], "seller_id": listing["seller_id"],
                "fulfillment_method": "pickup_point" if listing.get("meetup_preference") == "pickup_point" else "direct_meetup",
                "pickup_location_id": None, "status": "arranging", "kind": kind,
                "offered_listing_id": chosen["id"] if chosen else None,
                "price_egp": int(request.get("price_egp") or 0), "buyer_fee_egp": int(request.get("fee_egp") or 0),
                "seller_fee_egp": seller_fee, "buyer_held_egp": int(request.get("held_egp") or 0),
                "seller_held_egp": seller_fee if kind == "exchange" else 0,
                "buyer_discount_egp": int(request.get("fee_discount_egp") or 0), "seller_discount_egp": seller_discount,
                "buyer_confirmed_at": None, "seller_confirmed_at": None,
                "created_at": now, "updated_at": now, "completed_at": None,
            }
            if kind == "exchange":
                self.wallet.adjust(actor_id, -seller_fee, "hold", request_id=request["id"], transaction_id=transaction["id"])
            TRANSACTIONS.insert(0, transaction)
            if promo:
                MEMORY_PROMO_STORE.record(promo, actor_id, request["id"], "seller", seller_discount)
            if free_request:
                MEMORY_REFERRAL_STORE.use_credit(actor_id, request["id"], "seller")

            reserved_ids = {listing["id"]} | ({chosen["id"]} if chosen else set())
            for item in LISTINGS:
                if item["id"] in reserved_ids:
                    item["status"] = "reserved"
            for other in REQUESTS:
                if other is not request and other["status"] == "pending" and other["listing_id"] in reserved_ids:
                    self._close_pending(other, "rejected")

            request.update(status="accepted", held_egp=0, updated_at=now)
            _notify(request["buyer_id"], "request_accepted", listing, actor_id=actor_id, request_id=request["id"])
            return {**_public_request(request), "free_request_used": free_request}

    # --- Handover ---

    def confirm(self, transaction_id: str, actor_id: str) -> dict[str, Any]:
        with self._lock:
            transaction = self._transaction_or_404(transaction_id)
            if actor_id not in {transaction["buyer_id"], transaction["seller_id"]}:
                raise HTTPException(status_code=403, detail="You are not authorized to complete this transaction")
            if transaction["status"] != "arranging":
                raise HTTPException(status_code=409, detail="Only arranging transactions can be completed")
            is_buyer = transaction["buyer_id"] == actor_id
            kind = transaction.get("kind", "free")
            now = _now()

            if kind == "sale" and not is_buyer:
                raise HTTPException(status_code=403, detail="Only the buyer can confirm receiving the book")
            if kind == "exchange":
                field = "buyer_confirmed_at" if is_buyer else "seller_confirmed_at"
                if transaction.get(field):
                    raise HTTPException(status_code=409, detail="You already confirmed this handover")
                transaction[field] = now
                transaction["updated_at"] = now
                if not (transaction.get("buyer_confirmed_at") and transaction.get("seller_confirmed_at")):
                    other = transaction["seller_id"] if is_buyer else transaction["buyer_id"]
                    _notify(other, "deal_confirmed_by_other", _get_listing_or_404(transaction["listing_id"]),
                            actor_id=actor_id, transaction=transaction)
                    return dict(transaction)

            if kind == "sale":
                payout = transaction["price_egp"] - transaction["seller_fee_egp"]
                self.wallet.adjust(transaction["seller_id"], payout, "payout", transaction_id=transaction["id"])
            self.wallet.record_fee(transaction.get("buyer_fee_egp", 0) + transaction.get("seller_fee_egp", 0), transaction["id"])

            listing = _get_listing_or_404(transaction["listing_id"])
            listing["status"] = FINAL_LISTING_STATUS[str(listing["listing_type"])]
            if transaction.get("offered_listing_id"):
                _get_listing_or_404(transaction["offered_listing_id"])["status"] = "exchanged"

            transaction.update(
                status="completed", completed_at=now, updated_at=now, buyer_held_egp=0, seller_held_egp=0,
                buyer_confirmed_at=transaction.get("buyer_confirmed_at") or now,
            )
            # Tell whoever did not just confirm (free deals: either side may complete, so both hear).
            if kind == "sale":
                _notify(transaction["seller_id"], "deal_completed", listing, transaction=transaction,
                        amount_egp=transaction["price_egp"] - transaction["seller_fee_egp"])
            else:
                for party in (transaction["buyer_id"], transaction["seller_id"]):
                    if kind == "free" or party != actor_id:
                        _notify(party, "deal_completed", listing, transaction=transaction)
            inviter_id = MEMORY_REFERRAL_STORE.reward_for(transaction)
            if inviter_id:
                MEMORY_NOTIFICATION_STORE.emit(inviter_id, "referral_reward",
                                               {"actor_name": _display_name(transaction["buyer_id"])})
            return dict(transaction)

    def cancel(self, transaction_id: str) -> dict[str, Any]:
        with self._lock:
            transaction = self._transaction_or_404(transaction_id)
            if transaction["status"] != "arranging":
                raise HTTPException(status_code=409, detail="Only arranging transactions can be completed")
            ids = {"request_id": transaction["request_id"], "transaction_id": transaction["id"]}
            self.wallet.adjust(transaction["buyer_id"], transaction.get("buyer_held_egp", 0), "refund", **ids)
            self.wallet.adjust(transaction["seller_id"], transaction.get("seller_held_egp", 0), "refund", **ids)
            MEMORY_PROMO_STORE.release(transaction["request_id"])
            MEMORY_REFERRAL_STORE.release(transaction["request_id"])
            for item in LISTINGS:
                if item["id"] in {transaction["listing_id"], transaction.get("offered_listing_id")} and item["status"] == "reserved":
                    item["status"] = "active"
            listing = _get_listing_or_404(transaction["listing_id"])
            _notify(transaction["buyer_id"], "deal_cancelled", listing, transaction=transaction,
                    amount_egp=transaction.get("buyer_held_egp"))
            _notify(transaction["seller_id"], "deal_cancelled", listing, transaction=transaction,
                    amount_egp=transaction.get("seller_held_egp"))
            transaction.update(status="cancelled", buyer_held_egp=0, seller_held_egp=0, updated_at=_now())
            return dict(transaction)

    def close_listing(self, listing_id: str, seller_id: str | None, next_status: str) -> dict[str, Any]:
        with self._lock:
            listing = _get_listing_or_404(listing_id)
            if seller_id is not None and listing["seller_id"] != seller_id:
                raise HTTPException(status_code=404, detail="Listing not found")
            if any(t["status"] == "arranging" and listing_id in {t["listing_id"], t.get("offered_listing_id")} for t in TRANSACTIONS):
                raise HTTPException(status_code=409, detail="This book is in an open handover. Finish or cancel the handover first.")
            # Status first, so refunded buyers hear "no longer available" rather than "declined".
            listing["status"] = next_status
            for request in REQUESTS:
                if request["listing_id"] == listing_id and request["status"] == "pending":
                    self._close_pending(request, "rejected")
            return dict(listing)

    # --- Read models ---

    def my_requests(self, user_id: str) -> list[dict[str, Any]]:
        rows = []
        for request in REQUESTS:
            listing = next((item for item in LISTINGS if item["id"] == request["listing_id"]), None)
            if listing is None or user_id not in {request["buyer_id"], listing["seller_id"]}:
                continue
            offers = [
                {"listing_id": o["id"], "title": o["title"], "author": o.get("author"), "condition": o.get("condition"),
                 "status": o["status"], "images": o.get("images", [])}
                for o in LISTINGS if o["id"] in request.get("offers", [])
            ]
            rows.append({**_public_request(request), "seller_id": listing["seller_id"], "listing_title": listing["title"],
                         "buyer_name": _display_name(request["buyer_id"]), "offers": offers})
        return rows

    def transactions(self, user_id: str | None, status: str | None = None) -> list[dict[str, Any]]:
        rows = []
        for transaction in TRANSACTIONS:
            if user_id is not None and user_id not in {transaction["buyer_id"], transaction["seller_id"]}:
                continue
            if status is not None and transaction["status"] != status:
                continue
            listing = next((item for item in LISTINGS if item["id"] == transaction["listing_id"]), {})
            offered = next((item for item in LISTINGS if item["id"] == transaction.get("offered_listing_id")), {})
            rows.append({
                **_transaction_defaults(), **transaction, "listing_title": listing.get("title"),
                "offered_listing_title": offered.get("title"),
                "buyer_name": _display_name(transaction["buyer_id"]), "seller_name": _display_name(transaction["seller_id"]),
                **self._last_message(transaction["id"]),
            })
        return rows

    def get_transaction(self, transaction_id: str) -> dict[str, Any] | None:
        return next((dict(t) for t in TRANSACTIONS if t["id"] == transaction_id), None)

    def list_messages(self, transaction_id: str) -> list[dict[str, Any]]:
        return [dict(m) for m in self.messages if m["transaction_id"] == transaction_id]

    def add_message(self, transaction_id: str, sender_id: str, body: str) -> dict[str, Any]:
        message = {"id": str(uuid4()), "transaction_id": transaction_id, "sender_id": sender_id, "body": body, "created_at": _now()}
        with self._lock:
            self.messages.append(message)
            transaction = self._transaction_or_404(transaction_id)
            recipient = transaction["seller_id"] if sender_id == transaction["buyer_id"] else transaction["buyer_id"]
            _notify(recipient, "message", _get_listing_or_404(transaction["listing_id"]), actor_id=sender_id,
                    transaction=transaction, preview=message_preview(body))
        return dict(message)

    # --- Helpers ---

    def _last_message(self, transaction_id: str) -> dict[str, Any]:
        last = next((m for m in reversed(self.messages) if m["transaction_id"] == transaction_id), None)
        return {"last_message_at": last["created_at"] if last else None, "last_message_sender_id": last["sender_id"] if last else None}

    def _close_pending(self, request: dict[str, Any], next_status: str) -> None:
        held = int(request.get("held_egp") or 0)
        self.wallet.adjust(request["buyer_id"], held, "refund", request_id=request["id"])
        MEMORY_PROMO_STORE.release(request["id"])
        MEMORY_REFERRAL_STORE.release(request["id"])
        request.update(status=next_status, held_egp=0, updated_at=_now())
        listing = _get_listing_or_404(request["listing_id"])
        if next_status == "cancelled":
            _notify(listing["seller_id"], "request_cancelled", listing, actor_id=request["buyer_id"], request_id=request["id"])
        else:
            kind = "request_rejected" if next_status == "rejected" and listing["status"] == "active" else "request_unavailable"
            _notify(request["buyer_id"], kind, listing, request_id=request["id"], amount_egp=held)

    @staticmethod
    def _transaction_or_404(transaction_id: str) -> dict[str, Any]:
        transaction = next((t for t in TRANSACTIONS if t["id"] == transaction_id), None)
        if transaction is None:
            raise HTTPException(status_code=404, detail="Transaction not found")
        return transaction


def _notify(user_id: str, kind: str, listing: dict[str, Any], *, actor_id: str | None = None,
            request_id: str | None = None, transaction: dict[str, Any] | None = None,
            amount_egp: int | None = None, preview: str | None = None) -> None:
    """Memory-mode mirror of the migration 011 triggers."""
    MEMORY_NOTIFICATION_STORE.emit(user_id, kind, {
        "listing_title": listing.get("title"),
        "actor_name": _display_name(actor_id) if actor_id else None,
        "amount_egp": amount_egp or None,
        "preview": preview,
    }, transaction_id=transaction["id"] if transaction else None,
        request_id=transaction["request_id"] if transaction else request_id, listing_id=listing.get("id"))


def _is_own_active_listing(listing_id: str, owner_id: str) -> bool:
    listing = next((item for item in LISTINGS if item["id"] == listing_id), None)
    return listing is not None and listing["seller_id"] == owner_id and listing["status"] == "active"


def _public_request(request: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in request.items() if k != "offers"}


def _transaction_defaults() -> dict[str, Any]:
    return {
        "kind": "free", "offered_listing_id": None, "price_egp": 0, "buyer_fee_egp": 0, "seller_fee_egp": 0,
        "buyer_held_egp": 0, "seller_held_egp": 0, "buyer_confirmed_at": None, "seller_confirmed_at": None,
        "buyer_discount_egp": 0, "seller_discount_egp": 0,
    }


def _display_name(user_id: str) -> str:
    name = MEMORY_AUTH_STORE.get_display_name(user_id)
    if name:
        return name
    listing = next((item for item in LISTINGS if item["seller_id"] == user_id), None)
    return str(listing["seller_name"]) if listing else user_id


def _is_uuid(value: str) -> bool:
    try:
        UUID(value)
    except (ValueError, TypeError):
        return False
    return True


def _require_uuid(value: str, detail: str, status: int = 404) -> None:
    if not _is_uuid(value):
        raise HTTPException(status_code=status, detail=detail)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


MEMORY_DEAL_STORE = MemoryDealStore()


def get_deal_store() -> SupabaseDealStore | MemoryDealStore:
    supabase = get_supabase_service()
    return SupabaseDealStore(supabase) if supabase is not None else MEMORY_DEAL_STORE
