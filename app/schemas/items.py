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