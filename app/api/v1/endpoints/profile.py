from __future__ import annotations
from fastapi import Header
from fastapi import APIRouter

from api.deps import _require_auth
from core.supabase import get_supabase_service
from crud.crud_deals import get_deal_store
from crud.crud_item import LISTINGS, _normalize_listing

profile_router = APIRouter(prefix="/me", tags=["profile"])

@profile_router.get("/listings")
def get_my_listings(authorization: str | None = Header(default=None)) -> dict[str, object]:
    user_id = _require_auth(authorization)
    supabase = get_supabase_service()
    if supabase is not None:
        return {"items": supabase.my_listings(user_id)}

    my_listings = [
        _normalize_listing(item).model_dump(mode="json")
        for item in LISTINGS
        if item.get("seller_id") == user_id
    ]
    return {"items": my_listings}

@profile_router.get("/requests")
def get_my_requests(authorization: str | None = Header(default=None)) -> dict[str, object]:
    user_id = _require_auth(authorization)
    return {"items": get_deal_store().my_requests(user_id)}

@profile_router.get("/transactions")
def get_my_transactions(authorization: str | None = Header(default=None)) -> dict[str, object]:
    user_id = _require_auth(authorization)
    return {"items": get_deal_store().transactions(user_id)}
