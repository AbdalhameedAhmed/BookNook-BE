"""The signed-in user's wallet: balance, charging options, top-up submissions, and withdrawal requests."""

from __future__ import annotations

import re
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status

from api.deps import get_current_user_id
from core.config import (
    IMAGE_SIGNATURES,
    MAX_PENDING_TOPUPS_PER_USER,
    MAX_PENDING_WITHDRAWALS_PER_USER,
    MAX_RECEIPT_SIZE_BYTES,
    MAX_TOPUP_EGP,
    MIN_TOPUP_EGP,
    TOPUP_METHODS,
    platform_fee_egp,
)
from core.push import dispatch_pending_notifications
from core.rate_limit import topup_by_user, withdrawal_by_user
from crud.crud_referrals import get_referral_store
from crud.crud_wallet import charging_options, ewallet_recipient_number, get_wallet_store
from schemas.wallet import WithdrawalCreate

router = APIRouter(prefix="/wallet", tags=["wallet"])
CurrentUserId = Annotated[str, Depends(get_current_user_id)]

# Egyptian mobile numbers (Vodafone, Etisalat/e&, Orange, WE): 11 digits starting 010/011/012/015.
EGYPT_MOBILE = re.compile(r"^01[0125]\d{8}$")
# Fields shown to the wallet owner. Reviewer identity and the storage path stay internal.
OWNER_FIELDS = ("id", "method", "sender_number", "recipient_number", "amount_egp", "status",
                "review_note", "created_at", "reviewed_at")
WITHDRAWAL_OWNER_FIELDS = ("id", "method", "recipient_number", "amount_egp", "status", "review_note",
                           "created_at", "reviewed_at")


@router.get("")
def get_wallet(user_id: CurrentUserId) -> dict[str, object]:
    store = get_wallet_store()
    summary = store.summary(user_id)
    return {
        "balance_egp": summary["balance_egp"],
        # Money taken from the balance for open requests and handovers; refunded or paid out later.
        "held_egp": summary["held_egp"],
        "entries": summary["entries"],
        "options": charging_options(),
        "platform_fee_egp": platform_fee_egp(),
        # Fees that will be waived automatically (earned by inviting friends; see docs/referrals.md).
        "free_requests_available": get_referral_store().available(user_id),
        "topups": [_owner_view(row) for row in store.list_topups(user_id=user_id)],
        "withdrawals": [_withdrawal_view(row) for row in store.list_withdrawals(user_id=user_id)],
    }


@router.post("/topups", status_code=status.HTTP_201_CREATED)
def create_topup(
    sender_number: Annotated[str, Form(max_length=20)],
    amount_egp: Annotated[int, Form(ge=MIN_TOPUP_EGP, le=MAX_TOPUP_EGP)],
    receipt: Annotated[UploadFile, File()],
    user_id: CurrentUserId,
    method: Annotated[str, Form()] = "e_wallet",
) -> dict[str, object]:
    if method not in TOPUP_METHODS:
        raise HTTPException(status_code=400, detail="Unsupported charging method")
    sender = normalize_egypt_mobile(sender_number)
    if sender is None:
        raise HTTPException(status_code=400, detail="Enter a valid Egyptian mobile number, like 01012345678")

    content = receipt.file.read(MAX_RECEIPT_SIZE_BYTES + 1)
    if len(content) > MAX_RECEIPT_SIZE_BYTES:
        raise HTTPException(status_code=413, detail="Receipt images must be 20 MB or smaller")
    content_type = receipt.content_type or ""
    signature_check = IMAGE_SIGNATURES.get(content_type)
    if not content or signature_check is None or not signature_check(content):
        raise HTTPException(status_code=415, detail="Upload a valid JPEG, PNG, or WebP image")

    store = get_wallet_store()
    if store.count_pending(user_id) >= MAX_PENDING_TOPUPS_PER_USER:
        raise HTTPException(status_code=409, detail="You have too many top-ups waiting for review")
    topup_by_user.hit(user_id)

    receipt_path = store.save_receipt(user_id, content, content_type)
    topup = store.create_topup({
        "user_id": user_id,
        "method": method,
        "sender_number": sender,
        "recipient_number": ewallet_recipient_number(),
        "amount_egp": amount_egp,
        "receipt_path": receipt_path,
    })
    dispatch_pending_notifications()
    return _owner_view(topup)


@router.post("/withdrawals", status_code=status.HTTP_201_CREATED)
def create_withdrawal(payload: WithdrawalCreate, user_id: CurrentUserId) -> dict[str, object]:
    """Takes the amount from the balance now; a moderator sends it by hand and marks it paid (or returns it)."""
    recipient = normalize_egypt_mobile(payload.recipient_number)
    if recipient is None:
        raise HTTPException(status_code=400, detail="Enter a valid Egyptian mobile number, like 01012345678")
    withdrawal_by_user.hit(user_id)
    withdrawal = get_wallet_store().request_withdrawal(
        user_id, recipient, payload.amount_egp, MAX_PENDING_WITHDRAWALS_PER_USER,
    )
    dispatch_pending_notifications()
    return _withdrawal_view(withdrawal)


def normalize_egypt_mobile(value: str) -> str | None:
    digits = re.sub(r"[\s\-()]", "", value)
    for prefix in ("+20", "0020"):
        if digits.startswith(prefix):
            digits = "0" + digits[len(prefix):]
    return digits if EGYPT_MOBILE.match(digits) else None


def _owner_view(row: dict[str, object]) -> dict[str, object]:
    return {key: row.get(key) for key in OWNER_FIELDS}


def _withdrawal_view(row: dict[str, object]) -> dict[str, object]:
    return {key: row.get(key) for key in WITHDRAWAL_OWNER_FIELDS}
