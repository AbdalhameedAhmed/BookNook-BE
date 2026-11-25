from __future__ import annotations

from pydantic import BaseModel, Field


class TopupReject(BaseModel):
    note: str | None = Field(default=None, max_length=500)


class PromoCreate(BaseModel):
    code: str = Field(..., pattern=r"^\s*[A-Za-z0-9_-]{3,32}\s*$")
    discount_egp: int = Field(..., ge=1, le=10_000)
    # Number of book deals the code can be used on, across all users.
    max_uses: int = Field(..., ge=1, le=1_000_000)
    # Optional cap on how many book deals one person can use it on. None = no per-person limit.
    max_uses_per_user: int | None = Field(default=None, ge=1, le=1_000)


class PromoUpdate(BaseModel):
    active: bool
