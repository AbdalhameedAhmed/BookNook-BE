from __future__ import annotations

from pydantic import BaseModel, Field

from core.config import MAX_WITHDRAWAL_EGP, MIN_WITHDRAWAL_EGP


class TopupReject(BaseModel):
    note: str | None = Field(default=None, max_length=500)


class WithdrawalCreate(BaseModel):
    # The user's own e-wallet number; normalised and checked in the endpoint.
    recipient_number: str = Field(..., max_length=20)
    amount_egp: int = Field(..., ge=MIN_WITHDRAWAL_EGP, le=MAX_WITHDRAWAL_EGP)


class WithdrawalReview(BaseModel):
    """Paid: the transfer reference. Rejected: the reason. Shown to the user either way."""
    note: str | None = Field(default=None, max_length=500)


class PromoCreate(BaseModel):
    code: str = Field(..., pattern=r"^\s*[A-Za-z0-9_-]{3,32}\s*$")
    discount_egp: int = Field(..., ge=1, le=10_000)
    # Number of book deals the code can be used on, across all users.
    max_uses: int = Field(..., ge=1, le=1_000_000)


class PromoUpdate(BaseModel):
    active: bool
