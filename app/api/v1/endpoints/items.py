from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, File, Header, HTTPException, Query, UploadFile, status

from api.deps import (
    _get_authenticated_user,
    _require_auth,
    get_authenticated_user,
    get_current_user_id,
    is_moderator,
)
from core.config import (
    IMAGE_SIGNATURES,
    MAX_BOOK_IMAGE_SIZE_BYTES,
    VALID_CONDITIONS,
    VALID_GOVERNORATES,
    platform_fee_egp,
)
from core.push import dispatch_pending_notifications
from core.supabase import get_supabase_service
from core.rate_limit import promo_check_by_user
from crud.crud_deals import FINAL_LISTING_STATUS, get_deal_store
from crud.crud_promos import PROMO_INVALID, get_promo_store
from crud.crud_notifications import MEMORY_NOTIFICATION_STORE
from crud.crud_profiles import memory_profile
from crud.crud_user import MEMORY_AUTH_STORE
from crud.crud_item import (
    LISTINGS,
    REPORTS,
    _get_listing_or_404,
    _normalize_listing,
)
from schemas.items import ListingCreate, ListingUpdate, ReportCreate, RequestCreate, RequestUpdate

router = APIRouter()

def _check_governorate(governorate: str | None) -> None:
    if governorate is not None and governorate not in VALID_GOVERNORATES:
        raise HTTPException(status_code=400, detail="governorate must be one of Egypt's governorates")


@router.get("/listings")
def get_listings(
    q: str | None = None,
    listing_type: str | None = None,
    condition: str | None = None,
    governorate: str | None = None,
    page: int = 1,
    page_size: int = 12,
    seller_id: str | None = Query(default=None, max_length=64),
    sort: Literal["newest", "price_asc"] = "newest",
) -> dict[str, object]:
    """Public catalogue of available books. `seller_id` limits it to one person's shelf (profile page)."""
    if page < 1:
        raise HTTPException(status_code=400, detail="page must be at least 1")
    if page_size < 1:
        raise HTTPException(status_code=400, detail="page_size must be at least 1")
    _check_governorate(governorate)

    supabase = get_supabase_service()

    if supabase is not None:
        items, total = supabase.list_listings(
            query=q,
            listing_type=listing_type,
            condition=condition,
            governorate=governorate,
            page=page,
            page_size=page_size,
            seller_id=seller_id,
            sort=sort,
        )
        return {"items": items, "total": total, "page": page, "page_size": page_size}

    items = [
        _normalize_listing(item) for item in LISTINGS
        if item["status"] == "active" and (seller_id is None or item["seller_id"] == seller_id)
    ]
    if sort == "price_asc":
        items.sort(key=lambda item: (item.price_egp is None, item.price_egp or 0))

    if q:
        query = q.lower().strip()
        items = [
            item for item in items if query in f"{item.title} {item.author or ''}".lower()
        ]

    if listing_type:
        items = [item for item in items if item.listing_type == listing_type]

    if condition:
        items = [item for item in items if item.condition == condition]

    if governorate:
        items = [item for item in items if item.governorate == governorate]

    total = len(items)
    start = (page - 1) * page_size
    end = start + page_size
    page_items = items[start:end]

    return {
        "items": [item.model_dump(mode="json") for item in page_items],
        "total": total,
        "page": page,
        "page_size": page_size,
    }

@router.get("/listings/{listing_id}")
def get_listing_by_id(listing_id: str) -> dict[str, object]:
    supabase = get_supabase_service()
    if supabase is not None:
        return supabase.get_listing(listing_id)
    item = _get_listing_or_404(listing_id)
    return _normalize_listing(item).model_dump(mode="json")

@router.post("/listing-images", status_code=status.HTTP_201_CREATED)
def upload_listing_image(
    file: UploadFile = File(...),
    authorization: str | None = Header(default=None),
) -> dict[str, str]:
    seller_id = _require_auth(authorization)
    content = file.file.read(MAX_BOOK_IMAGE_SIZE_BYTES + 1)
    if len(content) > MAX_BOOK_IMAGE_SIZE_BYTES:
        raise HTTPException(status_code=413, detail="Book photos must be 20 MB or smaller")

    content_type = file.content_type or ""
    signature_check = IMAGE_SIGNATURES.get(content_type)
    if not content or signature_check is None or not signature_check(content):
        raise HTTPException(status_code=415, detail="Upload a valid JPEG, PNG, or WebP image")

    supabase = get_supabase_service()
    if supabase is None:
        raise HTTPException(status_code=503, detail="Book photo uploads require Supabase storage")

    return {"url": supabase.upload_listing_image(seller_id, content, content_type)}

@router.post("/listings", status_code=status.HTTP_201_CREATED)
def create_listing(payload: ListingCreate, authorization: str | None = Header(default=None)) -> dict[str, object]:
    user_id = _require_auth(authorization)

    if payload.condition not in VALID_CONDITIONS:
        raise HTTPException(status_code=400, detail="condition must be one of: like_new, good, acceptable, heavily_used")

    _check_governorate(payload.governorate)

    if payload.listing_type == "sale":
        if payload.price_egp is None or payload.price_egp < 0:
            raise HTTPException(status_code=400, detail="price_egp is required and must be non-negative for sale listings")
    if payload.listing_type in {"exchange", "free"} and payload.price_egp is not None and payload.price_egp < 0:
        raise HTTPException(status_code=400, detail="price_egp must be non-negative when provided")

    supabase = get_supabase_service()
    if supabase is not None:
        return supabase.create_listing(payload.model_dump(), user_id)

    listing_id = f"listing-{len(LISTINGS) + 1}"
    item = {
        "id": listing_id,
        "title": payload.title,
        "author": payload.author,
        "description": payload.description,
        "condition": payload.condition,
        "listing_type": payload.listing_type,
        "price_egp": payload.price_egp,
        "status": "active",
        "governorate": payload.governorate,
        "area": payload.area,
        "meetup_preference": payload.meetup_preference,
        "seller_id": user_id,
        "seller_name": MEMORY_AUTH_STORE.get_display_name(user_id) or user_id.replace("-", " ").title(),
        "images": payload.images,
        "created_at": "2026-10-24T00:00:00Z",
    }
    LISTINGS.insert(0, item)
    return {**item, "id": listing_id}

@router.patch("/listings/{listing_id}")
def update_listing(
    listing_id: str,
    payload: ListingUpdate,
    authorization: str | None = Header(default=None),
) -> dict[str, object]:
    user_id = _require_auth(authorization)
    if "governorate" in payload.model_fields_set:
        if payload.governorate is None:
            raise HTTPException(status_code=400, detail="governorate can't be removed from a listing")
        _check_governorate(payload.governorate)
    supabase = get_supabase_service()
    if supabase is not None:
        update_data = payload.model_dump(exclude_unset=True, exclude={"status"})
        if "condition" in update_data and update_data["condition"] not in VALID_CONDITIONS:
            raise HTTPException(status_code=400, detail="condition must be one of: like_new, good, acceptable, heavily_used")
        current = supabase.get_listing(listing_id, seller_id=user_id)
        next_type = update_data.get("listing_type", current["listing_type"])
        next_price = update_data.get("price_egp", current.get("price_egp"))
        if next_type == "sale" and (next_price is None or next_price < 0):
            raise HTTPException(status_code=400, detail="price_egp is required and must be non-negative for sale listings")
        if next_type in {"exchange", "free"} and next_price is not None and next_price < 0:
            raise HTTPException(status_code=400, detail="price_egp must be non-negative when provided")
        return supabase.update_listing(listing_id, user_id, update_data)

    item = _get_listing_or_404(listing_id)

    if item["seller_id"] != user_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="You can only edit your own listings")

    # Status only changes through deactivate / mark-complete / handovers, which keep held money consistent.
    update_data = payload.model_dump(exclude_unset=True, exclude={"status"})
    if not update_data:
        return item

    if "condition" in update_data and update_data["condition"] not in VALID_CONDITIONS:
        raise HTTPException(status_code=400, detail="condition must be one of: like_new, good, acceptable, heavily_used")

    next_type = update_data.get("listing_type", item["listing_type"])
    next_price = update_data.get("price_egp", item.get("price_egp"))
    if next_type == "sale" and (next_price is None or next_price < 0):
        raise HTTPException(status_code=400, detail="price_egp is required and must be non-negative for sale listings")
    if next_type in {"exchange", "free"} and next_price is not None and next_price < 0:
        raise HTTPException(status_code=400, detail="price_egp must be non-negative when provided")

    item.update(update_data)
    return item

@router.post("/listings/{listing_id}/deactivate")
def deactivate_listing(
    listing_id: str, authorization: str | None = Header(default=None)
) -> dict[str, str]:
    user_id = _require_auth(authorization)
    _get_owned_listing(listing_id, user_id, "You can only deactivate your own listings")
    get_deal_store().close_listing(listing_id, user_id, "inactive")
    dispatch_pending_notifications()
    return {"status": "inactive", "listing_id": listing_id}

@router.post("/listings/{listing_id}/mark-complete")
def mark_listing_complete(
    listing_id: str, authorization: str | None = Header(default=None)
) -> dict[str, str]:
    """Owner closes a listing that changed hands outside Rfoof. Pending requests are refunded."""
    user_id = _require_auth(authorization)
    listing = _get_owned_listing(listing_id, user_id, "You can only complete your own listings")
    if listing["status"] not in {"active", "reserved"}:
        raise HTTPException(status_code=400, detail="Only active or reserved listings can be marked complete")
    final_status = FINAL_LISTING_STATUS[str(listing["listing_type"])]
    get_deal_store().close_listing(listing_id, user_id, final_status)
    dispatch_pending_notifications()
    return {"status": final_status, "listing_id": listing_id}

@router.post("/listings/{listing_id}/requests", status_code=status.HTTP_201_CREATED)
def create_request(
    listing_id: str,
    payload: RequestCreate,
    authorization: str | None = Header(default=None),
) -> dict[str, object]:
    """Sale: holds price + fee from the buyer's wallet. Exchange: holds the fee and records the offered books."""
    buyer_id = _require_auth(authorization)
    request = get_deal_store().create_request(
        listing_id, buyer_id, payload.message, payload.offered_listing_ids, platform_fee_egp(), _promo(payload.promo_code)
    )
    dispatch_pending_notifications()
    return request

@router.patch("/requests/{request_id}")
def update_request(
    request_id: str,
    payload: RequestUpdate,
    authorization: str | None = Header(default=None),
) -> dict[str, object]:
    user_id = _require_auth(authorization)
    request = get_deal_store().update_request(
        request_id, user_id, payload.status, payload.chosen_listing_id, platform_fee_egp(), _promo(payload.promo_code)
    )
    dispatch_pending_notifications()
    return request

@router.get("/fees")
def get_fees() -> dict[str, int]:
    return {"platform_fee_egp": platform_fee_egp()}

@router.get("/promo-codes/{code}")
def check_promo_code(code: str, authorization: str | None = Header(default=None)) -> dict[str, object]:
    """Preview a code's discount before sending/accepting a request. Does not use it up."""
    user_id = _require_auth(authorization)
    promo_check_by_user.hit(user_id)
    if _promo(code) is None or len(code) > 32:
        raise HTTPException(status_code=400, detail=PROMO_INVALID)
    return get_promo_store().check(code)


def _promo(code: str | None) -> str | None:
    return code.strip() or None if code else None

@router.post("/reports", status_code=status.HTTP_201_CREATED)
def create_report(payload: ReportCreate, authorization: str | None = Header(default=None)) -> dict[str, object]:
    reporter_id = _require_auth(authorization)

    if payload.listing_id is None and payload.reported_user_id is None:
        raise HTTPException(status_code=400, detail="Either listing_id or reported_user_id must be provided")
    if payload.reported_user_id == reporter_id:
        raise HTTPException(status_code=400, detail="You cannot report yourself")

    supabase = get_supabase_service()
    if supabase is not None:
        report = supabase.create_report(payload.model_dump(), reporter_id)
        dispatch_pending_notifications()
        return report

    if payload.listing_id is not None:
        _get_listing_or_404(payload.listing_id)

    if payload.reported_user_id is not None:
        if memory_profile(payload.reported_user_id) is None:
            raise HTTPException(status_code=404, detail="Reported user not found")

    report_id = f"report-{len(REPORTS) + 1}"
    report = {
        "id": report_id,
        "reporter_id": reporter_id,
        "listing_id": payload.listing_id,
        "reported_user_id": payload.reported_user_id,
        "reason": payload.reason,
        "details": payload.details,
        "status": "open",
        "created_at": "2026-10-24T00:00:00Z",
        "updated_at": "2026-10-24T00:00:00Z",
    }
    REPORTS.insert(0, report)
    # Memory-mode mirror of the migration 012 report trigger.
    listing = next((item for item in LISTINGS if item["id"] == payload.listing_id), None)
    MEMORY_NOTIFICATION_STORE.emit_to_moderators(
        reporter_id, "report_new",
        {
            "actor_name": MEMORY_AUTH_STORE.get_display_name(reporter_id),
            "listing_title": listing["title"] if listing else None,
            "preview": payload.reason[:80],
        },
        listing_id=payload.listing_id,
    )
    dispatch_pending_notifications()
    return report

@router.get("/reports")
def get_reports(authorization: str | None = Header(default=None)) -> dict[str, object]:
    current_user = _get_authenticated_user(authorization)
    user_id = str(current_user["id"])
    supabase = get_supabase_service()
    if supabase is not None:
        return {"items": supabase.my_reports(user_id, is_moderator(current_user))}

    if user_id != "demo-user-1":
        user_reports = [
            report
            for report in REPORTS
            if report["reporter_id"] == user_id
            or report.get("reported_user_id") == user_id
        ]
        return {"items": user_reports}

    return {"items": REPORTS}

@router.patch("/reports/{report_id}")
def update_report(
    report_id: str,
    status: str,
    authorization: str | None = Header(default=None),
) -> dict[str, object]:
    current_user = _get_authenticated_user(authorization)
    user_id = str(current_user["id"])
    supabase = get_supabase_service()
    if supabase is not None:
        if not is_moderator(current_user):
            raise HTTPException(status_code=403, detail="Only moderators can resolve reports")
        if status not in {"open", "reviewing", "resolved", "dismissed"}:
            raise HTTPException(status_code=400, detail="Unsupported report status")
        return supabase.update_report(report_id, status)

    report = next((item for item in REPORTS if item["id"] == report_id), None)
    if report is None:
        raise HTTPException(status_code=404, detail="Report not found")

    if user_id != "demo-user-1":
        raise HTTPException(status_code=403, detail="Only moderators can resolve reports")

    if status not in {"open", "reviewing", "resolved", "dismissed"}:
        raise HTTPException(status_code=400, detail="Unsupported report status")

    report["status"] = status
    report["updated_at"] = "2026-10-24T00:00:00Z"
    return report

@router.post("/transactions/{transaction_id}/complete")
def complete_transaction(
    transaction_id: str, authorization: str | None = Header(default=None)
) -> dict[str, object]:
    """Sale: the buyer confirms receipt and the seller is paid. Exchange: completes once both parties confirm."""
    user_id = _require_auth(authorization)
    transaction = get_deal_store().confirm(transaction_id, user_id)
    dispatch_pending_notifications()
    return transaction


def _get_owned_listing(listing_id: str, user_id: str, forbidden_detail: str) -> dict[str, object]:
    supabase = get_supabase_service()
    if supabase is not None:
        return supabase.get_listing(listing_id, seller_id=user_id)
    listing = _get_listing_or_404(listing_id)
    if listing["seller_id"] != user_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=forbidden_detail)
    return listing
