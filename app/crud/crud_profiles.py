"""Public seller profiles. Supabase: the public_profile RPC (migration 015). Memory: the same shape."""

from __future__ import annotations

from collections import Counter
from typing import Any

from core.supabase import get_supabase_service
from crud.crud_item import LISTINGS, TRANSACTIONS
from crud.crud_user import MEMORY_AUTH_STORE


def memory_profile(user_id: str) -> dict[str, Any] | None:
    """Demo-mode mirror of public_profile. Unknown ids (no account and no listings) return None."""
    account = MEMORY_AUTH_STORE.get_user(user_id)
    own = [item for item in LISTINGS if item.get("seller_id") == user_id]
    if account is None and not own:
        return None
    active = sorted((item for item in own if item.get("status") == "active"),
                    key=lambda item: str(item.get("created_at")), reverse=True)
    by_type = Counter(str(item["listing_type"]) for item in active)
    newest = max(own, key=lambda item: str(item.get("created_at")), default=None)
    return {
        "id": user_id,
        "display_name": MEMORY_AUTH_STORE.get_display_name(user_id) or (newest or {}).get("seller_name") or "Rfoof reader",
        "avatar_url": MEMORY_AUTH_STORE.get_avatar_url(user_id),
        "area": active[0]["area"] if active else None,
        "member_since": (account or {}).get("password_changed_at") or min(
            (str(item.get("created_at")) for item in own), default=None),
        "available": {
            "total": len(active),
            "sale": by_type["sale"], "exchange": by_type["exchange"], "free": by_type["free"],
        },
        "handed_over": sum(
            1 for t in TRANSACTIONS if t.get("status") == "completed"
            and (t.get("seller_id") == user_id or (t.get("buyer_id") == user_id and t.get("offered_listing_id")))
        ),
    }


def get_public_profile(user_id: str) -> dict[str, Any] | None:
    supabase = get_supabase_service()
    return supabase.public_profile(user_id) if supabase is not None else memory_profile(user_id)
