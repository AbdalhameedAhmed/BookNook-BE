"""Promo codes: a fixed EGP discount on the platform fee (see migration 009 and docs/wallet.md).

``max_uses`` is the number of book deals a code can be used on, across all users. A use
is released (given back) when its request is refunded or its handover is cancelled.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import UUID, uuid4

from fastapi import HTTPException

from core.supabase import get_supabase_service

PROMO_INVALID = "This promo code isn't valid"
PROMO_EXHAUSTED = "This promo code has been fully used"
PROMO_NOT_APPLICABLE = "Promo codes only apply to sale and exchange fees"
PROMO_TAKEN = "A promo code with this name already exists"


def normalize_code(code: str) -> str:
    return code.strip().upper()


class SupabasePromoStore:
    def __init__(self, service: Any) -> None:
        self.service = service

    def check(self, code: str) -> dict[str, Any]:
        return self.service.rpc("check_promo_code", {"p_code": code})

    def create(self, code: str, discount_egp: int, max_uses: int, created_by: str) -> dict[str, Any]:
        return self.service.rpc("create_promo_code", {
            "p_code": code, "p_discount": discount_egp, "p_max_uses": max_uses, "p_created_by": created_by,
        })

    def list(self) -> list[dict[str, Any]]:
        return self.service.rpc("admin_promo_codes", {}) or []

    def set_active(self, promo_id: str, active: bool) -> dict[str, Any]:
        try:
            UUID(promo_id)
        except ValueError as error:
            raise HTTPException(status_code=404, detail="Promo code not found") from error
        rows = self.service.rest(
            "PATCH", "promo_codes", params={"id": f"eq.{promo_id}", "select": "*"},
            payload={"active": active, "updated_at": _now()}, prefer="return=representation",
        ).json()
        if not rows:
            raise HTTPException(status_code=404, detail="Promo code not found")
        return rows[0]


class MemoryPromoStore:
    """Demo/test-only mirror of the migration 009 promo functions."""

    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self.codes: dict[str, dict[str, Any]] = {}
        self.redemptions: list[dict[str, Any]] = []

    # --- Used by the memory deal store ---

    def discount_for(self, code: str, fee: int) -> tuple[dict[str, Any], int]:
        """Validates a code for a fee without recording a use. Returns (promo, discount)."""
        if fee <= 0:
            raise HTTPException(status_code=400, detail=PROMO_NOT_APPLICABLE)
        promo = self._usable(code)
        return promo, min(promo["discount_egp"], fee)

    def record(self, promo: dict[str, Any], user_id: str, request_id: str, party: str, discount: int) -> None:
        self.redemptions.append({
            "id": str(uuid4()), "promo_code_id": promo["id"], "user_id": user_id, "request_id": request_id,
            "party": party, "discount_egp": discount, "released_at": None, "created_at": _now(),
        })

    def release(self, request_id: str) -> None:
        for redemption in self.redemptions:
            if redemption["request_id"] == request_id and redemption["released_at"] is None:
                redemption["released_at"] = _now()

    # --- API ---

    def check(self, code: str) -> dict[str, Any]:
        promo = self._usable(code)
        return {"code": promo["code"], "discount_egp": promo["discount_egp"]}

    def create(self, code: str, discount_egp: int, max_uses: int, created_by: str) -> dict[str, Any]:
        normalized = normalize_code(code)
        if any(p["code"] == normalized for p in self.codes.values()):
            raise HTTPException(status_code=409, detail=PROMO_TAKEN)
        now = _now()
        promo = {
            "id": str(uuid4()), "code": normalized, "discount_egp": discount_egp, "max_uses": max_uses,
            "active": True, "created_by": created_by, "created_at": now, "updated_at": now,
        }
        self.codes[promo["id"]] = promo
        return self._view(promo)

    def list(self) -> list[dict[str, Any]]:
        return [self._view(p) for p in sorted(self.codes.values(), key=lambda p: p["created_at"], reverse=True)]

    def set_active(self, promo_id: str, active: bool) -> dict[str, Any]:
        promo = self.codes.get(promo_id)
        if promo is None:
            raise HTTPException(status_code=404, detail="Promo code not found")
        promo.update(active=active, updated_at=_now())
        return dict(promo)

    def _usable(self, code: str) -> dict[str, Any]:
        normalized = normalize_code(code)
        promo = next((p for p in self.codes.values() if p["code"] == normalized), None)
        if promo is None or not promo["active"]:
            raise HTTPException(status_code=400, detail=PROMO_INVALID)
        if self._uses(promo["id"]) >= promo["max_uses"]:
            raise HTTPException(status_code=409, detail=PROMO_EXHAUSTED)
        return promo

    def _uses(self, promo_id: str) -> int:
        return sum(1 for r in self.redemptions if r["promo_code_id"] == promo_id and r["released_at"] is None)

    def _view(self, promo: dict[str, Any]) -> dict[str, Any]:
        live = [r for r in self.redemptions if r["promo_code_id"] == promo["id"] and r["released_at"] is None]
        return {**promo, "uses_count": len(live), "discount_given_egp": sum(r["discount_egp"] for r in live)}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


MEMORY_PROMO_STORE = MemoryPromoStore()


def get_promo_store() -> SupabasePromoStore | MemoryPromoStore:
    supabase = get_supabase_service()
    return SupabasePromoStore(supabase) if supabase is not None else MEMORY_PROMO_STORE
