"""In-app chat between the two parties of an accepted request (polled, not real-time)."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from api.deps import get_current_user_id
from core.push import dispatch_pending_pushes
from core.rate_limit import message_by_user
from crud.crud_deals import get_deal_store

router = APIRouter(prefix="/transactions", tags=["handovers"])
CurrentUserId = Annotated[str, Depends(get_current_user_id)]


class MessageCreate(BaseModel):
    body: str = Field(..., min_length=1, max_length=1000)


@router.get("/{transaction_id}/messages")
def list_messages(transaction_id: str, user_id: CurrentUserId) -> dict[str, object]:
    _participant_transaction(transaction_id, user_id)
    return {"items": get_deal_store().list_messages(transaction_id)}


@router.post("/{transaction_id}/messages", status_code=status.HTTP_201_CREATED)
def send_message(transaction_id: str, payload: MessageCreate, user_id: CurrentUserId) -> dict[str, object]:
    transaction = _participant_transaction(transaction_id, user_id)
    if transaction["status"] != "arranging":
        raise HTTPException(status_code=409, detail="This handover is closed")
    body = payload.body.strip()
    if not body:
        raise HTTPException(status_code=422, detail="Message cannot be empty")
    message_by_user.hit(user_id)
    message = get_deal_store().add_message(transaction_id, user_id, body)
    dispatch_pending_pushes()
    return message


def _participant_transaction(transaction_id: str, user_id: str) -> dict[str, object]:
    transaction = get_deal_store().get_transaction(transaction_id)
    if transaction is None:
        raise HTTPException(status_code=404, detail="Transaction not found")
    if user_id not in {transaction["buyer_id"], transaction["seller_id"]}:
        raise HTTPException(status_code=403, detail="You are not part of this handover")
    return transaction
