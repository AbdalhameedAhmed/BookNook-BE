"""User/credential storage for authentication.

``get_auth_store()`` returns the Supabase-backed service in persistent mode and
an in-memory store in demo/test mode. Both expose the same methods.
"""

from __future__ import annotations

from datetime import datetime, timezone
from threading import Lock
from typing import Any
from uuid import uuid4

from core.supabase import get_supabase_service


class MemoryAuthStore:
    """Demo/test-only store. Data is lost when the process restarts."""

    def __init__(self) -> None:
        self._lock = Lock()
        self.reset()

    def reset(self) -> None:
        self.users: dict[str, dict[str, Any]] = {}
        self.display_names: dict[str, str] = {}
        self.tokens: dict[str, dict[str, Any]] = {}

    def find_user_by_email(self, email: str) -> dict[str, Any] | None:
        return next((dict(user) for user in self.users.values() if user["email"] == email), None)

    def get_user(self, user_id: str) -> dict[str, Any] | None:
        user = self.users.get(user_id)
        return dict(user) if user else None

    def get_display_name(self, user_id: str) -> str | None:
        return self.display_names.get(user_id)

    def register_user(self, email: str, password_hash: str, display_name: str) -> dict[str, Any] | None:
        with self._lock:
            if any(user["email"] == email for user in self.users.values()):
                return None
            user_id = str(uuid4())
            self.users[user_id] = {
                "id": user_id,
                "email": email,
                "password_hash": password_hash,
                "role": "user",
                "email_confirmed_at": None,
                "password_changed_at": _now(),
            }
            self.display_names[user_id] = display_name
            return dict(self.users[user_id])

    def issue_auth_token(self, user_id: str, purpose: str, token_hash: str, expires_at: datetime) -> None:
        with self._lock:
            for token in self.tokens.values():
                if token["user_id"] == user_id and token["purpose"] == purpose and token["used_at"] is None:
                    token["used_at"] = _now()
            self.tokens[token_hash] = {
                "user_id": user_id,
                "purpose": purpose,
                "expires_at": expires_at,
                "used_at": None,
            }

    def confirm_user_email(self, token_hash: str) -> str | None:
        with self._lock:
            user_id = self._consume(token_hash, "email_confirmation")
            if user_id:
                self.users[user_id]["email_confirmed_at"] = self.users[user_id]["email_confirmed_at"] or _now()
            return user_id

    def reset_user_password(self, token_hash: str, password_hash: str) -> str | None:
        with self._lock:
            user_id = self._consume(token_hash, "password_reset")
            if user_id:
                user = self.users[user_id]
                user["password_hash"] = password_hash
                user["password_changed_at"] = _now()
                user["email_confirmed_at"] = user["email_confirmed_at"] or _now()
            return user_id

    def _consume(self, token_hash: str, purpose: str) -> str | None:
        token = self.tokens.get(token_hash)
        if not token or token["purpose"] != purpose or token["used_at"] is not None or token["expires_at"] <= _now():
            return None
        token["used_at"] = _now()
        return token["user_id"]


def _now() -> datetime:
    return datetime.now(timezone.utc)


MEMORY_AUTH_STORE = MemoryAuthStore()


def get_auth_store() -> Any:
    supabase = get_supabase_service()
    return supabase if supabase is not None else MEMORY_AUTH_STORE
