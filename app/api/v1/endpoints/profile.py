from __future__ import annotations
from fastapi import Header
from fastapi import APIRouter

from app.api.deps import _require_auth
from app.core.supabase import get_supabase_service
from app.crud.crud_item import LISTINGS, REQUESTS, TRANSACTIONS, _normalize_listing

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
    supabase = get_supabase_service()
    if supabase is not None:
        return {"items": supabase.my_requests(user_id)}

    user_requests = [
        request
        for request in REQUESTS
        if request["buyer_id"] == user_id or request["seller_id"] == user_id
    ]
    return {"items": user_requests}

@profile_router.get("/transactions")
def get_my_transactions(authorization: str | None = Header(default=None)) -> dict[str, object]:
    user_id = _require_auth(authorization)
    supabase = get_supabase_service()
    if supabase is not None:
        return {"items": supabase.my_transactions(user_id)}

    my_transactions = [
        transaction
        for transaction in TRANSACTIONS
        if transaction["buyer_id"] == user_id or transaction["seller_id"] == user_id
    ]
    return {"items": my_transactions}