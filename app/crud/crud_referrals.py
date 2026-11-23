"""Invite links and free requests (see migration 016 and docs/referrals.md).

A friend who signs up from someone's invite link is recorded as their referral. When the friend's
first sale or exchange handover as the buyer/requester completes (with someone other than the
inviter), the inviter earns one free request: one platform fee waived, on either side. It is used
automatically on their next fee-paying request or acceptance unless they enter a promo code, and
given back if that request is refunded or its handover cancelled.
"""

from __future__ import annotations

import re
import secrets
from datetime import datetime, timezone
from threading import RLock
from typing import Any

from core.supabase import get_supabase_service

INVITE_CODE_PATTERN = re.compile(r"^[A-Z0-9]{8}$")
INVITE_CODE_ALPHABET = "0123456789ABCDEF"


def normalize_invite_code(code: str | None) -> str | None:
    """Uppercased code if it looks valid, otherwise None (unknown codes never block sign-up)."""
    if not code:
        return None
    normalized = code.strip().upper()
    return normalized if INVITE_CODE_PATTERN.match(normalized) else None


class SupabaseReferralStore:
    def __init__(self, service: Any) -> None:
        self.service = service

    def summary(self, user_id: str) -> dict[str, Any]:
        return self.service.rpc("referral_summary", {"p_user_id": user_id})

    def available(self, user_id: str) -> int:
        return int(self.service.rpc("available_referral_credits", {"p_user_id": user_id}) or 0)


class MemoryReferralStore:
    """Demo/test-only mirror of the migration 016 functions and triggers."""

    def __init__(self) -> None:
        self._lock = RLock()
        self.reset()

    def reset(self) -> None:
        self.codes: dict[str, str] = {}  # user_id -> invite code
        self.referrals: dict[str, dict[str, Any]] = {}  # invitee_id -> row

    # --- Codes and sign-up ---

    def ensure_code(self, user_id: str) -> str:
        with self._lock:
            if user_id not in self.codes:
                taken = set(self.codes.values())
                code = _random_code()
                while code in taken:
                    code = _random_code()
                self.codes[user_id] = code
            return self.codes[user_id]

    def record_signup(self, invitee_id: str, invite_code: str | None) -> None:
        code = normalize_invite_code(invite_code)
        if code is None:
            return
        with self._lock:
            inviter_id = next((uid for uid, c in self.codes.items() if c == code), None)
            if inviter_id is None or inviter_id == invitee_id or invitee_id in self.referrals:
                return
            self.referrals[invitee_id] = {
                "invitee_id": invitee_id, "inviter_id": inviter_id, "created_at": _now(),
                "rewarded_at": None, "reward_transaction_id": None,
                "credit_request_id": None, "credit_party": None, "credit_used_at": None,
            }

    # --- Credits ---

    def available(self, user_id: str) -> int:
        return len(self._available_rows(user_id))

    def use_credit(self, user_id: str, request_id: str, party: str) -> bool:
        with self._lock:
            rows = sorted(self._available_rows(user_id), key=lambda r: r["rewarded_at"])
            if not rows:
                return False
            rows[0].update(credit_request_id=request_id, credit_party=party, credit_used_at=_now())
            return True

    def release(self, request_id: str) -> None:
        with self._lock:
            for row in self.referrals.values():
                if row["credit_request_id"] == request_id:
                    row.update(credit_request_id=None, credit_party=None, credit_used_at=None)

    def reward_for(self, transaction: dict[str, Any]) -> str | None:
        """Called when a handover completes. Returns the inviter who earned a free request, if any."""
        if transaction.get("kind") not in {"sale", "exchange"}:
            return None
        with self._lock:
            row = self.referrals.get(transaction["buyer_id"])
            if row is None or row["rewarded_at"] or row["inviter_id"] == transaction["seller_id"]:
                return None
            row.update(rewarded_at=_now(), reward_transaction_id=transaction["id"])
            return str(row["inviter_id"])

    # --- API ---

    def summary(self, user_id: str) -> dict[str, Any]:
        mine = [r for r in self.referrals.values() if r["inviter_id"] == user_id]
        return {
            "invite_code": self.ensure_code(user_id),
            "friends_joined": len(mine),
            "free_requests_earned": sum(1 for r in mine if r["rewarded_at"]),
            "free_requests_available": self.available(user_id),
        }

    def _available_rows(self, user_id: str) -> list[dict[str, Any]]:
        return [r for r in self.referrals.values()
                if r["inviter_id"] == user_id and r["rewarded_at"] and r["credit_request_id"] is None]


def _random_code() -> str:
    return "".join(secrets.choice(INVITE_CODE_ALPHABET) for _ in range(8))


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


MEMORY_REFERRAL_STORE = MemoryReferralStore()


def get_referral_store() -> SupabaseReferralStore | MemoryReferralStore:
    supabase = get_supabase_service()
    return SupabaseReferralStore(supabase) if supabase is not None else MEMORY_REFERRAL_STORE
