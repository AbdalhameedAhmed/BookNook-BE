"""Moderator-only site overview and wallet top-up review."""

from __future__ import annotations

from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status

from api.deps import get_authenticated_user, is_moderator
from core.push import dispatch_pending_notifications
from crud.crud_deals import get_deal_store
from crud.crud_wallet import get_wallet_store
from crud.crud_promos import get_promo_store
from schemas.wallet import PromoCreate, PromoUpdate, TopupReject


def require_moderator(user: Annotated[dict[str, object], Depends(get_authenticated_user)]) -> dict[str, object]:
    if not is_moderator(user):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Moderator access required")
    return user


Moderator = Annotated[dict[str, object], Depends(require_moderator)]


router = APIRouter(prefix="/admin", tags=["admin"], dependencies=[Depends(require_moderator)])
# Internal storage location; moderators get a short-lived URL instead.
HIDDEN_FIELDS = {"receipt_path"}


@router.get("/overview")
def get_overview() -> dict[str, object]:
    return get_wallet_store().overview()


@router.get("/wallet-topups")
def list_topups(status: Literal["pending", "approved", "rejected"] = "pending") -> dict[str, object]:
    store = get_wallet_store()
    rows = store.list_topups(status=status)
    urls = store.receipt_urls([str(row["receipt_path"]) for row in rows])
    return {
        "items": [
            {**{k: v for k, v in row.items() if k not in HIDDEN_FIELDS}, "receipt_url": urls.get(str(row["receipt_path"]))}
            for row in rows
        ]
    }


@router.post("/wallet-topups/{topup_id}/approve")
def approve_topup(topup_id: UUID, moderator: Moderator) -> dict[str, object]:
    topup = get_wallet_store().approve_topup(str(topup_id), str(moderator["id"]))
    dispatch_pending_notifications()
    return _public(topup)


@router.post("/wallet-topups/{topup_id}/reject")
def reject_topup(
    topup_id: UUID,
    moderator: Moderator,
    payload: TopupReject | None = None,
) -> dict[str, object]:
    note = (payload.note or "").strip() if payload else ""
    topup = get_wallet_store().reject_topup(str(topup_id), str(moderator["id"]), note or None)
    dispatch_pending_notifications()
    return _public(topup)


@router.get("/transactions")
def list_transactions(status: Literal["arranging", "completed", "cancelled"] = "arranging") -> dict[str, object]:
    return {"items": get_deal_store().transactions(None, status)}


@router.post("/transactions/{transaction_id}/cancel")
def cancel_transaction(transaction_id: str) -> dict[str, object]:
    """For handovers that fall through after acceptance: refunds everything held to both parties."""
    transaction = get_deal_store().cancel(transaction_id)
    dispatch_pending_notifications()
    return transaction


@router.get("/promo-codes")
def list_promo_codes() -> dict[str, object]:
    return {"items": get_promo_store().list()}


@router.post("/promo-codes", status_code=status.HTTP_201_CREATED)
def create_promo_code(payload: PromoCreate, moderator: Moderator) -> dict[str, object]:
    return get_promo_store().create(
        payload.code.strip(), payload.discount_egp, payload.max_uses, str(moderator["id"]), payload.max_uses_per_user
    )


@router.patch("/promo-codes/{promo_id}")
def update_promo_code(promo_id: str, payload: PromoUpdate) -> dict[str, object]:
    """Switch a code on or off. Deals that already used it keep their discount."""
    return get_promo_store().set_active(promo_id, payload.active)


def _public(row: dict[str, object]) -> dict[str, object]:
    return {k: v for k, v in row.items() if k not in HIDDEN_FIELDS}
