from __future__ import annotations

from typing import Literal

from fastapi import FastAPI, File, Header, HTTPException, UploadFile, status
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field, model_validator

from app.supabase_service import get_supabase_service

app = FastAPI(
    title="BookNook API",
    version="0.1.0",
    description="Backend for the BookNook marketplace MVP.",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

VALID_CONDITIONS = {"like_new", "good", "acceptable", "heavily_used"}
DEMO_USERS = {"demo-user-1", "demo-user-2"}
MAX_BOOK_IMAGE_SIZE_BYTES = 2 * 1024 * 1024
IMAGE_SIGNATURES = {
    "image/jpeg": lambda content: content.startswith(b"\xff\xd8\xff"),
    "image/png": lambda content: content.startswith(b"\x89PNG\r\n\x1a\n"),
    "image/webp": lambda content: len(content) >= 12 and content.startswith(b"RIFF") and content[8:12] == b"WEBP",
}


class ListingCreate(BaseModel):
    title: str = Field(..., min_length=2)
    author: str | None = None
    description: str | None = None
    condition: str
    listing_type: Literal["sale", "exchange", "free"]
    price_egp: int | None = None
    area: str = Field(..., min_length=2)
    meetup_preference: Literal["direct_meetup", "pickup_point", "either"] = "direct_meetup"
    images: list[str] = Field(default_factory=list)


class ListingUpdate(BaseModel):
    title: str | None = None
    author: str | None = None
    description: str | None = None
    condition: str | None = None
    listing_type: Literal["sale", "exchange", "free"] | None = None
    price_egp: int | None = None
    area: str | None = None
    meetup_preference: Literal["direct_meetup", "pickup_point", "either"] | None = None
    status: str | None = None
    images: list[str] | None = None


class Listing(BaseModel):
    id: str
    title: str
    author: str | None = None
    description: str | None = None
    condition: str
    listing_type: str
    price_egp: int | None = None
    status: str
    area: str
    meetup_preference: str
    seller_id: str
    seller_name: str
    images: list[str] = Field(default_factory=list)
    created_at: str


class RequestCreate(BaseModel):
    message: str | None = None


class RequestUpdate(BaseModel):
    status: Literal["accepted", "rejected", "cancelled"]


class ReportCreate(BaseModel):
    listing_id: str | None = None
    reported_user_id: str | None = None
    reason: str = Field(..., min_length=2, max_length=200)
    details: str | None = None


LISTINGS: list[dict[str, object]] = [
    {
        "id": "listing-1",
        "title": "Linear Algebra for Beginners",
        "author": "Mina Saad",
        "description": "Used but clean copy with chapter notes and an index tabbed for quick revision.",
        "condition": "good",
        "listing_type": "sale",
        "price_egp": 180,
        "status": "active",
        "area": "Fayoum Center",
        "meetup_preference": "direct_meetup",
        "seller_id": "demo-user-1",
        "seller_name": "Amal",
        "images": ["https://images.example.com/linear-algebra.jpg"],
        "created_at": "2026-10-01T10:30:00Z",
    },
    {
        "id": "listing-2",
        "title": "Modern Biology",
        "author": "Nadia Farag",
        "description": "Good condition with diagrams, chapter summaries, and a few highlighted pages.",
        "condition": "like_new",
        "listing_type": "exchange",
        "price_egp": None,
        "status": "active",
        "area": "Tamiyya",
        "meetup_preference": "pickup_point",
        "seller_id": "demo-user-2",
        "seller_name": "Khaled",
        "images": ["https://images.example.com/biology.jpg"],
        "created_at": "2026-10-02T08:00:00Z",
    },
    {
        "id": "listing-3",
        "title": "Fayoum History Guide",
        "author": "Hossam Youssef",
        "description": "A local-interest reference book for school projects and general reading.",
        "condition": "acceptable",
        "listing_type": "free",
        "price_egp": None,
        "status": "active",
        "area": "Sinnuris",
        "meetup_preference": "either",
        "seller_id": "demo-user-1",
        "seller_name": "Sara",
        "images": ["https://images.example.com/history.jpg"],
        "created_at": "2026-10-04T09:15:00Z",
    },
    {
        "id": "listing-4",
        "title": "Basic Chemistry Workbook",
        "author": "Sameh Ibrahim",
        "description": "Sold as a set with practice questions and worked explanations.",
        "condition": "good",
        "listing_type": "sale",
        "price_egp": 120,
        "status": "sold",
        "area": "Fayoum Center",
        "meetup_preference": "direct_meetup",
        "seller_id": "demo-user-2",
        "seller_name": "Noha",
        "images": ["https://images.example.com/chemistry.jpg"],
        "created_at": "2026-09-25T13:00:00Z",
    },
]


def _normalize_listing(item: dict[str, object]) -> Listing:
    return Listing.model_validate(item)


def _require_auth(authorization: str | None) -> str:
    return str(_get_authenticated_user(authorization)["id"])


def _get_authenticated_user(authorization: str | None) -> dict[str, object]:
    if not authorization:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication required")

    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired token")

    supabase = get_supabase_service()
    if supabase is not None:
        return supabase.verify_access_token(token)

    if token not in DEMO_USERS:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired token")
    return {"id": token, "app_metadata": {}}


def _get_listing_or_404(listing_id: str) -> dict[str, object]:
    for item in LISTINGS:
        if item["id"] == listing_id:
            return item
    raise HTTPException(status_code=404, detail="Listing not found")


REQUESTS: list[dict[str, object]] = []
TRANSACTIONS: list[dict[str, object]] = []
REPORTS: list[dict[str, object]] = []


def _get_request_or_404(request_id: str) -> dict[str, object]:
    for item in REQUESTS:
        if item["id"] == request_id:
            return item
    raise HTTPException(status_code=404, detail="Request not found")


def _get_transaction_or_404(transaction_id: str) -> dict[str, object]:
    for item in TRANSACTIONS:
        if item["id"] == transaction_id:
            return item
    raise HTTPException(status_code=404, detail="Transaction not found")


@app.get("/health")
def health_check() -> dict[str, str]:
    return {"status": "ok", "service": "booknook-backend"}


@app.get("/api/v1/health")
def api_health_check() -> dict[str, str]:
    return {"status": "ok", "service": "booknook-backend"}


@app.get("/api/v1/listings")
def get_listings(
    q: str | None = None,
    listing_type: str | None = None,
    condition: str | None = None,
    page: int = 1,
    page_size: int = 12,
) -> dict[str, object]:
    if page < 1:
        raise HTTPException(status_code=400, detail="page must be at least 1")
    if page_size < 1:
        raise HTTPException(status_code=400, detail="page_size must be at least 1")

    supabase = get_supabase_service()
    if supabase is not None:
        items, total = supabase.list_listings(
            query=q,
            listing_type=listing_type,
            condition=condition,
            page=page,
            page_size=page_size,
        )
        return {"items": items, "total": total, "page": page, "page_size": page_size}

    items = [_normalize_listing(item) for item in LISTINGS if item["status"] == "active"]

    if q:
        query = q.lower().strip()
        items = [
            item for item in items if query in f"{item.title} {item.author or ''}".lower()
        ]

    if listing_type:
        items = [item for item in items if item.listing_type == listing_type]

    if condition:
        items = [item for item in items if item.condition == condition]

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


@app.get("/api/v1/listings/{listing_id}")
def get_listing_by_id(listing_id: str) -> dict[str, object]:
    supabase = get_supabase_service()
    if supabase is not None:
        return supabase.get_listing(listing_id)
    item = _get_listing_or_404(listing_id)
    return _normalize_listing(item).model_dump(mode="json")


@app.post("/api/v1/listing-images", status_code=status.HTTP_201_CREATED)
def upload_listing_image(
    file: UploadFile = File(...),
    authorization: str | None = Header(default=None),
) -> dict[str, str]:
    seller_id = _require_auth(authorization)
    content = file.file.read(MAX_BOOK_IMAGE_SIZE_BYTES + 1)
    if len(content) > MAX_BOOK_IMAGE_SIZE_BYTES:
        raise HTTPException(status_code=413, detail="Book photos must be 2 MB or smaller")

    content_type = file.content_type or ""
    signature_check = IMAGE_SIGNATURES.get(content_type)
    if not content or signature_check is None or not signature_check(content):
        raise HTTPException(status_code=415, detail="Upload a valid JPEG, PNG, or WebP image")

    supabase = get_supabase_service()
    if supabase is None:
        raise HTTPException(status_code=503, detail="Book photo uploads require Supabase storage")

    return {"url": supabase.upload_listing_image(seller_id, content, content_type)}


@app.post("/api/v1/listings", status_code=status.HTTP_201_CREATED)
def create_listing(payload: ListingCreate, authorization: str | None = Header(default=None)) -> dict[str, object]:
    user_id = _require_auth(authorization)

    if payload.condition not in VALID_CONDITIONS:
        raise HTTPException(status_code=400, detail="condition must be one of: like_new, good, acceptable, heavily_used")

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
        "area": payload.area,
        "meetup_preference": payload.meetup_preference,
        "seller_id": user_id,
        "seller_name": user_id.replace("-", " ").title(),
        "images": payload.images,
        "created_at": "2026-10-24T00:00:00Z",
    }
    LISTINGS.insert(0, item)
    return {**item, "id": listing_id}


@app.patch("/api/v1/listings/{listing_id}")
def update_listing(
    listing_id: str,
    payload: ListingUpdate,
    authorization: str | None = Header(default=None),
) -> dict[str, object]:
    user_id = _require_auth(authorization)
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

    update_data = payload.model_dump(exclude_unset=True)
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
    item["status"] = update_data.get("status", item["status"])
    return item


@app.post("/api/v1/listings/{listing_id}/deactivate")
def deactivate_listing(listing_id: str, authorization: str | None = Header(default=None)) -> dict[str, str]:
    user_id = _require_auth(authorization)
    supabase = get_supabase_service()
    if supabase is not None:
        supabase.deactivate_listing(listing_id, user_id)
        return {"status": "inactive", "listing_id": listing_id}

    item = _get_listing_or_404(listing_id)

    if item["seller_id"] != user_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="You can only deactivate your own listings")

    item["status"] = "inactive"
    return {"status": "inactive", "listing_id": listing_id}


@app.post("/api/v1/listings/{listing_id}/mark-complete")
def mark_listing_complete(listing_id: str, authorization: str | None = Header(default=None)) -> dict[str, str]:
    user_id = _require_auth(authorization)
    supabase = get_supabase_service()

    if supabase is not None:
        listing = supabase.get_listing(listing_id, seller_id=user_id)
        if listing["status"] not in {"active", "reserved"}:
            raise HTTPException(status_code=400, detail="Only active or reserved listings can be marked complete")
        final_status = {
            "sale": "sold",
            "exchange": "exchanged",
            "free": "inactive",
        }[listing["listing_type"]]
        supabase.update_listing(listing_id, user_id, {"status": final_status})
        return {"status": final_status, "listing_id": listing_id}

    listing = _get_listing_or_404(listing_id)
    if listing["seller_id"] != user_id:
        raise HTTPException(status_code=403, detail="You can only complete your own listings")
    if listing["status"] not in {"active", "reserved"}:
        raise HTTPException(status_code=400, detail="Only active or reserved listings can be marked complete")

    final_status = {
        "sale": "sold",
        "exchange": "exchanged",
        "free": "inactive",
    }[str(listing["listing_type"])]
    listing["status"] = final_status
    return {"status": final_status, "listing_id": listing_id}


@app.get("/api/v1/me/listings")
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


@app.post("/api/v1/listings/{listing_id}/requests", status_code=status.HTTP_201_CREATED)
def create_request(
    listing_id: str,
    payload: RequestCreate,
    authorization: str | None = Header(default=None),
) -> dict[str, object]:
    buyer_id = _require_auth(authorization)
    supabase = get_supabase_service()
    if supabase is not None:
        return supabase.create_request(listing_id, buyer_id, payload.message)

    listing = _get_listing_or_404(listing_id)

    if listing["seller_id"] == buyer_id:
        raise HTTPException(status_code=403, detail="You cannot request your own listing")

    if listing["status"] != "active":
        raise HTTPException(status_code=400, detail="Inactive or sold listings cannot receive new requests")

    existing = any(
        request["listing_id"] == listing_id
        and request["buyer_id"] == buyer_id
        and request["status"] == "pending"
        for request in REQUESTS
    )
    if existing:
        raise HTTPException(status_code=409, detail="You already have a pending request for this listing")

    request_id = f"request-{len(REQUESTS) + 1}"
    request = {
        "id": request_id,
        "listing_id": listing_id,
        "buyer_id": buyer_id,
        "seller_id": listing["seller_id"],
        "message": payload.message or "",
        "status": "pending",
        "created_at": "2026-10-24T00:00:00Z",
        "updated_at": "2026-10-24T00:00:00Z",
    }
    REQUESTS.insert(0, request)
    return request


@app.patch("/api/v1/requests/{request_id}")
def update_request(
    request_id: str,
    payload: RequestUpdate,
    authorization: str | None = Header(default=None),
) -> dict[str, object]:
    user_id = _require_auth(authorization)
    supabase = get_supabase_service()
    if supabase is not None:
        return supabase.update_request(request_id, user_id, payload.status)

    request = _get_request_or_404(request_id)
    listing = _get_listing_or_404(request["listing_id"])

    if payload.status == "cancelled":
        if request["buyer_id"] != user_id:
            raise HTTPException(status_code=403, detail="Only the buyer can cancel a request")
        request["status"] = "cancelled"
        request["updated_at"] = "2026-10-24T00:00:00Z"
        return request

    if listing["seller_id"] != user_id:
        raise HTTPException(status_code=403, detail="Only the seller can accept or reject a request")

    if payload.status == "accepted":
        request["status"] = "accepted"
        listing["status"] = "reserved"
        request["updated_at"] = "2026-10-24T00:00:00Z"

        transaction_exists = any(
            transaction["listing_id"] == listing["id"]
            and transaction["buyer_id"] == request["buyer_id"]
            and transaction["seller_id"] == request["seller_id"]
            for transaction in TRANSACTIONS
        )
        if not transaction_exists:
            TRANSACTIONS.insert(
                0,
                {
                    "id": f"transaction-{len(TRANSACTIONS) + 1}",
                    "listing_id": listing["id"],
                    "buyer_id": request["buyer_id"],
                    "seller_id": request["seller_id"],
                    "fulfillment_method": listing.get("meetup_preference", "direct_meetup"),
                    "status": "arranging",
                    "created_at": "2026-10-24T00:00:00Z",
                    "updated_at": "2026-10-24T00:00:00Z",
                },
            )
        return request

    if payload.status == "rejected":
        request["status"] = "rejected"
        request["updated_at"] = "2026-10-24T00:00:00Z"
        return request

    raise HTTPException(status_code=400, detail="Unsupported request status update")


@app.get("/api/v1/me/requests")
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


@app.get("/api/v1/me/transactions")
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


@app.post("/api/v1/reports", status_code=status.HTTP_201_CREATED)
def create_report(payload: ReportCreate, authorization: str | None = Header(default=None)) -> dict[str, object]:
    reporter_id = _require_auth(authorization)

    if payload.listing_id is None and payload.reported_user_id is None:
        raise HTTPException(status_code=400, detail="Either listing_id or reported_user_id must be provided")

    supabase = get_supabase_service()
    if supabase is not None:
        return supabase.create_report(payload.model_dump(), reporter_id)

    if payload.listing_id is not None:
        _get_listing_or_404(payload.listing_id)

    if payload.reported_user_id is not None:
        if payload.reported_user_id not in DEMO_USERS:
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
    return report


@app.get("/api/v1/reports")
def get_reports(authorization: str | None = Header(default=None)) -> dict[str, object]:
    current_user = _get_authenticated_user(authorization)
    user_id = str(current_user["id"])
    supabase = get_supabase_service()
    if supabase is not None:
        app_metadata = current_user.get("app_metadata") or {}
        is_moderator = isinstance(app_metadata, dict) and app_metadata.get("role") == "moderator"
        return {"items": supabase.my_reports(user_id, is_moderator)}

    if user_id != "demo-user-1":
        user_reports = [
            report
            for report in REPORTS
            if report["reporter_id"] == user_id
            or report.get("reported_user_id") == user_id
        ]
        return {"items": user_reports}

    return {"items": REPORTS}


@app.patch("/api/v1/reports/{report_id}")
def update_report(
    report_id: str,
    status: str,
    authorization: str | None = Header(default=None),
) -> dict[str, object]:
    current_user = _get_authenticated_user(authorization)
    user_id = str(current_user["id"])
    supabase = get_supabase_service()
    if supabase is not None:
        app_metadata = current_user.get("app_metadata") or {}
        is_moderator = isinstance(app_metadata, dict) and app_metadata.get("role") == "moderator"
        if not is_moderator:
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


@app.post("/api/v1/transactions/{transaction_id}/complete")
def complete_transaction(transaction_id: str, authorization: str | None = Header(default=None)) -> dict[str, object]:
    user_id = _require_auth(authorization)
    supabase = get_supabase_service()
    if supabase is not None:
        return supabase.complete_transaction(transaction_id, user_id)

    transaction = _get_transaction_or_404(transaction_id)

    if transaction["buyer_id"] != user_id and transaction["seller_id"] != user_id:
        raise HTTPException(status_code=403, detail="You are not authorized to complete this transaction")

    if transaction["status"] != "arranging":
        raise HTTPException(status_code=400, detail="Only arranging transactions can be completed")

    transaction["status"] = "completed"
    transaction["updated_at"] = "2026-10-24T00:00:00Z"
    transaction["completed_at"] = "2026-10-24T00:00:00Z"

    listing = _get_listing_or_404(transaction["listing_id"])
    if listing["listing_type"] == "sale":
        listing["status"] = "sold"
    elif listing["listing_type"] == "exchange":
        listing["status"] = "exchanged"
    else:
        listing["status"] = "inactive"

    return transaction
