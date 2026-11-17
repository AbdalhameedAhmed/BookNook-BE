"""Moderator-only site overview and wallet top-up review."""

from __future__ import annotations

from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status

from api.deps import get_authenticated_user, is_moderator
from crud.crud_wallet import get_wallet_store
from schemas.wallet import TopupReject


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
    return _public(get_wallet_store().approve_topup(str(topup_id), str(moderator["id"])))


@router.post("/wallet-topups/{topup_id}/reject")
def reject_topup(
    topup_id: UUID,
    moderator: Moderator,
    payload: TopupReject | None = None,
) -> dict[str, object]:
    note = (payload.note or "").strip() if payload else ""
    return _public(get_wallet_store().reject_topup(str(topup_id), str(moderator["id"]), note or None))


def _public(row: dict[str, object]) -> dict[str, object]:
    return {k: v for k, v in row.items() if k not in HIDDEN_FIELDS}
