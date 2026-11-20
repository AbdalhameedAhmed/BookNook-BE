"""User/credential storage for authentication.

``get_auth_store()`` returns the Supabase-backed service in persistent mode and
an in-memory store in demo/test mode. Both expose the same methods.
"""

from __future__ import annotations

from datetime import datetime, timezone
from threading import RLock
from typing import Any
from uuid import uuid4

from core.supabase import get_supabase_service


class MemoryAuthStore:
    """Demo/test-only store. Data is lost when the process restarts."""

    def __init__(self) -> None:
        self._lock = RLock()
        self.reset()

    def reset(self) -> None:
        self.users: dict[str, dict[str, Any]] = {}
        self.display_names: dict[str, str] = {}
        self.tokens: dict[str, dict[str, Any]] = {}
        self.avatar_urls: dict[str, str] = {}

    def find_user_by_email(self, email: str) -> dict[str, Any] | None:
        return next((dict(user) for user in self.users.values() if user["email"] == email), None)

    def get_user(self, user_id: str) -> dict[str, Any] | None:
        user = self.users.get(user_id)
        return dict(user) if user else None

    def moderator_ids(self) -> list[str]:
        return [user_id for user_id, user in self.users.items() if user.get("role") == "moderator"]

    def get_display_name(self, user_id: str) -> str | None:
        return self.display_names.get(user_id)

    def get_avatar_url(self, user_id: str) -> str | None:
        return self.avatar_urls.get(user_id)

    def set_avatar_url(self, user_id: str, url: str | None) -> None:
        with self._lock:
            if url:
                self.avatar_urls[user_id] = url
            else:
                self.avatar_urls.pop(user_id, None)

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

    def request_email_change(self, user_id: str, new_email: str, token_hash: str, expires_at: datetime) -> None:
        """Mirror of request_email_change (migration 013)."""
        with self._lock:
            self.issue_auth_token(user_id, "email_change", token_hash, expires_at)
            self.tokens[token_hash]["new_email"] = new_email

    def confirm_email_change(self, token_hash: str) -> dict[str, Any] | None:
        """Mirror of confirm_email_change (migration 013)."""
        with self._lock:
            token = self.tokens.get(token_hash)
            user_id = self._consume(token_hash, "email_change")
            if not user_id or token is None:
                return None
            new_email = token["new_email"]
            if any(user["email"] == new_email and uid != user_id for uid, user in self.users.items()):
                return {"status": "taken"}
            user = self.users[user_id]
            old_email = user["email"]
            user.update(email=new_email, email_confirmed_at=_now())
            for other in self.tokens.values():
                if other["user_id"] == user_id and other["used_at"] is None \
                        and other["purpose"] in {"email_confirmation", "password_reset"}:
                    other["used_at"] = _now()
            return {"status": "changed", "user_id": user_id, "old_email": old_email, "new_email": new_email}

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
