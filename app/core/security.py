"""Password hashing, access tokens, and one-time email tokens."""

from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timezone

import bcrypt
import jwt
from fastapi import HTTPException, status

from core.auth_settings import get_auth_settings

JWT_ALGORITHM = "HS256"
JWT_ISSUER = "rfoof-api"
BCRYPT_ROUNDS = 12
# bcrypt only uses the first 72 bytes of a password; longer ones are rejected at validation.
MAX_PASSWORD_BYTES = 72

# Compared against when the email is unknown so login timing does not reveal registered emails.
_DUMMY_HASH = bcrypt.hashpw(b"rfoof-timing-equalizer", bcrypt.gensalt(rounds=BCRYPT_ROUNDS))


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt(rounds=BCRYPT_ROUNDS)).decode("ascii")


def verify_password(password: str, password_hash: str | None) -> bool:
    """Checks a password against a bcrypt hash (including $2a$ hashes copied from Supabase)."""
    candidate = password.encode("utf-8")
    if len(candidate) > MAX_PASSWORD_BYTES:
        return False
    if not password_hash:
        bcrypt.checkpw(candidate, _DUMMY_HASH)
        return False
    try:
        return bcrypt.checkpw(candidate, password_hash.encode("ascii"))
    except ValueError:
        return False


def create_access_token(user_id: str) -> tuple[str, int]:
    settings = get_auth_settings()
    now = datetime.now(timezone.utc)
    expires_at = now + settings.access_token_ttl
    claims = {
        "sub": user_id,
        "iss": JWT_ISSUER,
        "iat": int(now.timestamp()),
        "exp": int(expires_at.timestamp()),
        "typ": "access",
    }
    token = jwt.encode(claims, settings.jwt_secret, algorithm=JWT_ALGORITHM)
    return token, int(settings.access_token_ttl.total_seconds())


def decode_access_token(token: str) -> dict[str, object]:
    try:
        claims = jwt.decode(
            token,
            get_auth_settings().jwt_secret,
            algorithms=[JWT_ALGORITHM],
            issuer=JWT_ISSUER,
            options={"require": ["sub", "iat", "exp", "iss"]},
        )
    except jwt.PyJWTError as error:
        raise _invalid_token() from error
    if claims.get("typ") != "access" or not isinstance(claims.get("sub"), str):
        raise _invalid_token()
    return claims


def token_predates_password_change(claims: dict[str, object], password_changed_at: datetime | str | None) -> bool:
    if password_changed_at is None:
        return False
    if isinstance(password_changed_at, str):
        password_changed_at = datetime.fromisoformat(password_changed_at)
    issued_at = claims.get("iat")
    # JWT iat has 1-second precision, so compare against the whole second of the change.
    return not isinstance(issued_at, int) or issued_at < int(password_changed_at.timestamp())


def new_one_time_token() -> tuple[str, str]:
    """Returns (raw token for the email link, hash to store in the database)."""
    raw = secrets.token_urlsafe(32)
    return raw, hash_one_time_token(raw)


def hash_one_time_token(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _invalid_token() -> HTTPException:
    return HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired token")
