from __future__ import annotations

from fastapi import Header, HTTPException, status

from app.core.config import DEMO_USERS
from app.core.supabase import get_supabase_service


def get_authenticated_user(
    authorization: str | None = Header(default=None),
) -> dict[str, object]:
    if not authorization:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required",
        )

    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
        )

    supabase = get_supabase_service()
    if supabase is not None:
        return supabase.verify_access_token(token)

    if token not in DEMO_USERS:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
        )

    return {"id": token, "app_metadata": {}}


def get_current_user_id(
    authorization: str | None = Header(default=None),
) -> str:
    return str(get_authenticated_user(authorization)["id"])


# Compatibility aliases preserve the names used by the original endpoint code.
_get_authenticated_user = get_authenticated_user
_require_auth = get_current_user_id