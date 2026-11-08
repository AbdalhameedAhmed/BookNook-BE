from __future__ import annotations

from fastapi import HTTPException

from app.schemas.items import Listing


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

REQUESTS: list[dict[str, object]] = []

TRANSACTIONS: list[dict[str, object]] = []

REPORTS: list[dict[str, object]] = []

def _normalize_listing(item: dict[str, object]) -> Listing:
    return Listing.model_validate(item)

def _get_listing_or_404(listing_id: str) -> dict[str, object]:
    for item in LISTINGS:
        if item["id"] == listing_id:
            return item
    raise HTTPException(status_code=404, detail="Listing not found")

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