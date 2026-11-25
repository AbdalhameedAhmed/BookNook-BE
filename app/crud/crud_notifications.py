"""In-app notifications and Web Push subscriptions.

With Supabase, notification rows are written by the triggers in migrations 011, 012 and 018, so this store only
reads, marks read, and manages push subscriptions. The memory store mirrors those triggers through
``emit()``, which the memory deal and wallet stores call at the same points. See docs/notifications.md.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from threading import RLock
from typing import Any
from uuid import uuid4

from core.supabase import get_supabase_service
from crud.crud_user import MEMORY_AUTH_STORE

MAX_PAGE_SIZE = 100
MAX_SUBSCRIPTIONS_PER_USER = 10
PREVIEW_LENGTH = 80
PUSH_WINDOW = timedelta(days=1)
# While unread, these bump one row's count instead of adding rows: chat per handover, new books per user.
COLLAPSING_KINDS = frozenset({"message", "listing_new"})
READ_RETENTION = timedelta(days=90)


class SupabaseNotificationStore:
    def __init__(self, service: Any) -> None:
        self.service = service

    def list(self, user_id: str, limit: int, before: datetime | None) -> dict[str, Any]:
        return self.service.rpc("my_notifications", {
            "p_user_id": user_id, "p_limit": limit, "p_before": before.isoformat() if before else None,
        })

    def unread_count(self, user_id: str) -> int:
        return int(self.service.rpc("unread_notification_count", {"p_user_id": user_id}) or 0)

    def mark_read(self, user_id: str, ids: list[str] | None) -> int:
        return int(self.service.rpc("mark_notifications_read", {"p_user_id": user_id, "p_ids": ids}) or 0)

    def save_subscription(self, user_id: str, endpoint: str, p256dh: str, auth: str, lang: str, user_agent: str | None) -> None:
        self.service.rpc("save_push_subscription", {
            "p_user_id": user_id, "p_endpoint": endpoint, "p_p256dh": p256dh, "p_auth": auth,
            "p_lang": lang, "p_user_agent": user_agent,
        })

    def delete_subscription(self, endpoint: str, user_id: str | None = None) -> None:
        self.service.rpc("delete_push_subscription", {"p_endpoint": endpoint, "p_user_id": user_id})

    def claim_unpushed(self, limit: int) -> list[dict[str, Any]]:
        return self.service.rpc("claim_unpushed_notifications", {"p_limit": limit}) or []


class MemoryNotificationStore:
    """Demo/test-only mirror of migrations 011, 012 and 018. Keep them in step."""

    def __init__(self) -> None:
        self._lock = RLock()
        self.reset()

    def reset(self) -> None:
        self.notifications: list[dict[str, Any]] = []
        self.subscriptions: dict[str, dict[str, Any]] = {}

    def emit(self, user_id: str | None, kind: str, data: dict[str, Any], *, transaction_id: str | None = None,
             request_id: str | None = None, listing_id: str | None = None, topup_id: str | None = None) -> None:
        if not user_id:
            return
        data = {k: v for k, v in data.items() if v is not None}
        with self._lock:
            if kind in COLLAPSING_KINDS:
                row = next((n for n in self.notifications if n["user_id"] == user_id and n["kind"] == kind
                            and n["transaction_id"] == transaction_id and n["read_at"] is None), None)
                if row is not None:
                    row.update(count=row["count"] + 1, data=data, created_at=_now(), pushed_at=None)
                    if kind == "listing_new":
                        row["listing_id"] = listing_id
                    return
            self.notifications.append({
                "id": str(uuid4()), "user_id": user_id, "kind": kind, "transaction_id": transaction_id,
                "request_id": request_id, "listing_id": listing_id, "topup_id": topup_id, "data": data,
                "count": 1, "read_at": None, "pushed_at": None, "created_at": _now(),
            })

    def emit_to_moderators(self, actor_id: str | None, kind: str, data: dict[str, Any], *,
                           listing_id: str | None = None, topup_id: str | None = None) -> None:
        """Mirror of notify_moderators (migration 012): every moderator except the actor."""
        for moderator_id in MEMORY_AUTH_STORE.moderator_ids():
            if moderator_id != actor_id:
                self.emit(moderator_id, kind, data, listing_id=listing_id, topup_id=topup_id)

    def emit_new_listing(self, seller_id: str, listing_id: str, title: str, seller_name: str | None) -> None:
        """Mirror of notify_on_listing_created (migration 018): every user except the seller."""
        for user_id in list(MEMORY_AUTH_STORE.users):
            if user_id != seller_id:
                self.emit(user_id, "listing_new", {"listing_title": title, "actor_name": seller_name},
                          listing_id=listing_id)

    def list(self, user_id: str, limit: int, before: datetime | None) -> dict[str, Any]:
        with self._lock:
            rows = [n for n in self._newest_first() if n["user_id"] == user_id
                    and (before is None or n["created_at"] < _aware(before))]
            items = [_public(n) for n in rows[:max(1, min(limit, MAX_PAGE_SIZE))]]
            return {"items": items, "unread_count": self.unread_count(user_id)}

    def unread_count(self, user_id: str) -> int:
        return sum(1 for n in self.notifications if n["user_id"] == user_id and n["read_at"] is None)

    def mark_read(self, user_id: str, ids: list[str] | None) -> int:
        wanted = set(ids) if ids is not None else None
        now = _now()
        with self._lock:
            updated = 0
            for n in self.notifications:
                if n["user_id"] == user_id and n["read_at"] is None and (wanted is None or n["id"] in wanted):
                    n["read_at"] = now
                    updated += 1
            self.notifications = [n for n in self.notifications if not (
                n["user_id"] == user_id and n["read_at"] is not None and n["read_at"] < now - READ_RETENTION)]
            return updated

    def save_subscription(self, user_id: str, endpoint: str, p256dh: str, auth: str, lang: str, user_agent: str | None) -> None:
        with self._lock:
            existing = self.subscriptions.get(endpoint)
            self.subscriptions[endpoint] = {
                "user_id": user_id, "endpoint": endpoint, "p256dh": p256dh, "auth": auth, "lang": lang,
                "user_agent": (user_agent or "")[:300] or None,
                "created_at": existing["created_at"] if existing else _now(), "last_used_at": _now(),
            }
            mine = sorted((s for s in self.subscriptions.values() if s["user_id"] == user_id),
                          key=lambda s: s["last_used_at"], reverse=True)
            for stale in mine[MAX_SUBSCRIPTIONS_PER_USER:]:
                del self.subscriptions[stale["endpoint"]]

    def delete_subscription(self, endpoint: str, user_id: str | None = None) -> None:
        with self._lock:
            sub = self.subscriptions.get(endpoint)
            if sub is not None and (user_id is None or sub["user_id"] == user_id):
                del self.subscriptions[endpoint]

    def claim_unpushed(self, limit: int) -> list[dict[str, Any]]:
        cutoff = _now() - PUSH_WINDOW
        with self._lock:
            # Personal notifications before the new-book broadcast, as in migration 018.
            rows = sorted((n for n in self.notifications if n["pushed_at"] is None and n["created_at"] > cutoff),
                          key=lambda n: (n["kind"] == "listing_new", n["created_at"]))[:limit]
            claimed = []
            for n in rows:
                n["pushed_at"] = _now()
                subs = [{k: s[k] for k in ("endpoint", "p256dh", "auth", "lang")}
                        for s in self.subscriptions.values() if s["user_id"] == n["user_id"]]
                owner = MEMORY_AUTH_STORE.get_user(n["user_id"]) or {}
                email = owner.get("email") if owner.get("email_confirmed_at") else None
                claimed.append({**_public(n), "email": email, "subscriptions": subs})
            return claimed

    def _newest_first(self) -> list[dict[str, Any]]:
        return sorted(self.notifications, key=lambda n: n["created_at"], reverse=True)


def message_preview(body: str) -> str:
    return body.strip()[:PREVIEW_LENGTH]


def _public(row: dict[str, Any]) -> dict[str, Any]:
    out = {k: v for k, v in row.items() if k != "pushed_at"}
    for key in ("created_at", "read_at"):
        if isinstance(out.get(key), datetime):
            out[key] = out[key].isoformat()
    return out


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _now() -> datetime:
    return datetime.now(timezone.utc)


MEMORY_NOTIFICATION_STORE = MemoryNotificationStore()


def get_notification_store() -> SupabaseNotificationStore | MemoryNotificationStore:
    supabase = get_supabase_service()
    return SupabaseNotificationStore(supabase) if supabase is not None else MEMORY_NOTIFICATION_STORE
