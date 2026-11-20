"""Public seller profiles. No sign-in needed; only shows what is already public on listings."""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, HTTPException

from crud.crud_profiles import get_public_profile

router = APIRouter(prefix="/users", tags=["users"])


@router.get("/{user_id}")
def read_public_profile(user_id: str) -> dict[str, object]:
    """Name, photo, approximate area, member since, available books by type, books handed over.

    Never includes the email, phone number, wallet, or requests. The person's books come from
    `GET /listings?seller_id=…`.
    """
    profile = get_public_profile(user_id) if len(user_id) <= 64 else None
    if not profile:
        raise HTTPException(status_code=404, detail="Profile not found")
    member_since = profile.get("member_since")
    if isinstance(member_since, datetime):
        profile["member_since"] = member_since.isoformat()
    return profile
