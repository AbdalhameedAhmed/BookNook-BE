"""The signed-in user's notifications and Web Push subscriptions. See docs/notifications.md."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Query, status
from pydantic import BaseModel, Field, field_validator

from api.deps import get_current_user_id
from core.push import push_enabled, vapid_public_key
from core.rate_limit import push_subscribe_by_user
from crud.crud_notifications import get_notification_store

router = APIRouter(tags=["notifications"])
CurrentUserId = Annotated[str, Depends(get_current_user_id)]


class MarkRead(BaseModel):
    # Omitted or null marks everything read.
    ids: list[UUID] | None = Field(default=None, max_length=200)


class PushKeys(BaseModel):
    p256dh: str = Field(..., min_length=1, max_length=200)
    auth: str = Field(..., min_length=1, max_length=100)


class PushSubscriptionIn(BaseModel):
    endpoint: str = Field(..., min_length=10, max_length=1000)
    keys: PushKeys
    lang: Literal["en", "ar"] = "en"

    @field_validator("endpoint")
    @classmethod
    def https_only(cls, value: str) -> str:
        if not value.startswith("https://"):
            raise ValueError("Push endpoint must use https")
        return value


class PushSubscriptionOut(BaseModel):
    endpoint: str = Field(..., min_length=1, max_length=1000)


@router.get("/me/notifications")
def list_notifications(
    user_id: CurrentUserId,
    limit: Annotated[int, Query(ge=1, le=100)] = 30,
    before: datetime | None = None,
) -> dict[str, object]:
    return get_notification_store().list(user_id, limit, before)


@router.get("/me/notifications/unread-count")
def unread_count(user_id: CurrentUserId) -> dict[str, int]:
    return {"unread_count": get_notification_store().unread_count(user_id)}


@router.post("/me/notifications/read")
def mark_read(user_id: CurrentUserId, payload: MarkRead | None = None) -> dict[str, int]:
    ids = [str(i) for i in payload.ids] if payload and payload.ids is not None else None
    store = get_notification_store()
    updated = store.mark_read(user_id, ids)
    return {"updated": updated, "unread_count": store.unread_count(user_id)}


@router.get("/push/public-key")
def get_push_public_key() -> dict[str, str]:
    key = vapid_public_key()
    if not push_enabled() or key is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Push notifications are not configured")
    return {"public_key": key}


@router.post("/me/push-subscriptions", status_code=status.HTTP_204_NO_CONTENT)
def save_push_subscription(
    payload: PushSubscriptionIn,
    user_id: CurrentUserId,
    user_agent: Annotated[str | None, Header()] = None,
) -> None:
    if not push_enabled():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Push notifications are not configured")
    push_subscribe_by_user.hit(user_id)
    get_notification_store().save_subscription(
        user_id, payload.endpoint, payload.keys.p256dh, payload.keys.auth, payload.lang, (user_agent or "")[:300] or None
    )


@router.post("/me/push-subscriptions/remove", status_code=status.HTTP_204_NO_CONTENT)
def remove_push_subscription(payload: PushSubscriptionOut, user_id: CurrentUserId) -> None:
    """POST rather than DELETE-with-body, which some proxies drop. Only removes the caller's own endpoint."""
    get_notification_store().delete_subscription(payload.endpoint, user_id)
