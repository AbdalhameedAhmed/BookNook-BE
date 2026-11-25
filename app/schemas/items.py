from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class ListingCreate(BaseModel):
    title: str = Field(..., min_length=2)
    author: str | None = None
    description: str | None = None
    condition: str
    listing_type: Literal["sale", "exchange", "free"]
    price_egp: int | None = None
    governorate: str
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
    governorate: str | None = None
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
    governorate: str | None = None
    area: str
    meetup_preference: str
    seller_id: str
    seller_name: str
    images: list[str] = Field(default_factory=list)
    created_at: str

class RequestCreate(BaseModel):
    message: str | None = Field(default=None, max_length=1000)
    # Exchange requests only: the requester's own active listings offered in return.
    offered_listing_ids: list[str] = Field(default_factory=list, max_length=10)
    # Optional promo code lowering the requester's own fee.
    promo_code: str | None = Field(default=None, max_length=32)

class RequestUpdate(BaseModel):
    status: Literal["accepted", "rejected", "cancelled"]
    # Exchange acceptance only: which offered book the owner takes.
    chosen_listing_id: str | None = None
    # Acceptance only: optional promo code lowering the seller/owner's own fee.
    promo_code: str | None = Field(default=None, max_length=32)

class ReportCreate(BaseModel):
    listing_id: str | None = None
    reported_user_id: str | None = None
    reason: str = Field(..., min_length=2, max_length=200)
    details: str | None = None