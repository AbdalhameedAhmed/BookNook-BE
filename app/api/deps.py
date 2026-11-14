from __future__ import annotations

from fastapi import Header, HTTPException, status

from core.config import DEMO_USERS
from core.security import decode_access_token, token_predates_password_change
from core.supabase import get_supabase_service
from crud.crud_user import get_auth_store


def get_authenticated_user(
    authorization: str | None = Header(default=None),
) -> dict[str, object]:
    """Resolves the bearer access token issued by ``/api/v1/auth`` to the current user.

    The user row is re-read on every request so deleted users, role changes, and
    password resets (which revoke earlier tokens) take effect immediately.
    """
    if not authorization:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required",
        )

    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise _invalid_token()

    # Demo tokens only exist in in-memory demo/test mode, never with persistent storage.
    if get_supabase_service() is None and token in DEMO_USERS:
        return {"id": token, "email": f"{token}@demo.local", "role": "user"}

    claims = decode_access_token(token)
    user = get_auth_store().get_user(str(claims["sub"]))
    if user is None or token_predates_password_change(claims, user.get("password_changed_at")):
        raise _invalid_token()

    return {"id": str(user["id"]), "email": user["email"], "role": user.get("role") or "user"}


def get_current_user_id(
    authorization: str | None = Header(default=None),
) -> str:
    return str(get_authenticated_user(authorization)["id"])


def is_moderator(user: dict[str, object]) -> bool:
    return user.get("role") == "moderator"


def _invalid_token() -> HTTPException:
    return HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired token")


# Compatibility aliases preserve the names used by the original endpoint code.
_get_authenticated_user = get_authenticated_user
_require_auth = get_current_user_id
