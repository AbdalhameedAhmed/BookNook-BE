"""Account settings for the signed-in user: profile photo and email change. See docs/settings.md."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, File, HTTPException, UploadFile, status

from api.deps import get_current_user_id
from core import rate_limit
from core.auth_settings import EMAIL_CONFIRMATION_TTL
from core.config import IMAGE_SIGNATURES, MAX_BOOK_IMAGE_SIZE_BYTES
from core.email import send_email_change_email
from core.security import new_one_time_token, verify_password
from core.supabase import get_supabase_service
from crud.crud_user import get_auth_store
from schemas.auth import EmailChangeRequest, MessageResponse

router = APIRouter(prefix="/me", tags=["account"])

CurrentUserId = Annotated[str, Depends(get_current_user_id)]

MAX_AVATAR_SIZE_BYTES = MAX_BOOK_IMAGE_SIZE_BYTES
CHECK_NEW_INBOX = "Check the new inbox for a confirmation link. Your email changes once you open it."


@router.post("/avatar")
def upload_avatar(user_id: CurrentUserId, file: UploadFile = File(...)) -> dict[str, str]:
    """Replaces the profile photo. The previous photo is deleted from storage."""
    content = file.file.read(MAX_AVATAR_SIZE_BYTES + 1)
    if len(content) > MAX_AVATAR_SIZE_BYTES:
        raise HTTPException(status_code=413, detail="Profile photos must be 2 MB or smaller")
    content_type = file.content_type or ""
    signature_check = IMAGE_SIGNATURES.get(content_type)
    if not content or signature_check is None or not signature_check(content):
        raise HTTPException(status_code=415, detail="Upload a valid JPEG, PNG, or WebP image")

    supabase = get_supabase_service()
    if supabase is None:
        raise HTTPException(status_code=503, detail="Profile photo uploads require Supabase storage")
    rate_limit.avatar_upload_by_user.hit(user_id)

    previous = supabase.get_avatar_url(user_id)
    url = supabase.upload_avatar(user_id, content, content_type)
    supabase.set_avatar_url(user_id, url)
    if previous:
        supabase.delete_avatar_object(user_id, previous)
    return {"avatar_url": url}


@router.delete("/avatar", status_code=status.HTTP_204_NO_CONTENT)
def remove_avatar(user_id: CurrentUserId) -> None:
    store = get_auth_store()
    previous = store.get_avatar_url(user_id)
    store.set_avatar_url(user_id, None)
    supabase = get_supabase_service()
    if previous and supabase is not None:
        supabase.delete_avatar_object(user_id, previous)


@router.post("/email", status_code=status.HTTP_202_ACCEPTED, response_model=MessageResponse)
def request_email_change(
    payload: EmailChangeRequest, user_id: CurrentUserId, background: BackgroundTasks
) -> MessageResponse:
    """Mails a confirmation link to the new address. The account email changes only when it is opened.

    Requires the current password, so a borrowed signed-in device cannot take over the account.
    Whether the new address belongs to another account is not revealed.
    """
    rate_limit.email_change_by_user.hit(user_id)
    store = get_auth_store()
    user = store.get_user(user_id)
    if user is None or not verify_password(payload.current_password, user.get("password_hash")):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Current password is incorrect")
    if payload.new_email == user["email"]:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="That is already your email")

    if store.find_user_by_email(payload.new_email) is None:
        raw_token, token_hash = new_one_time_token()
        store.request_email_change(
            user_id, payload.new_email, token_hash, datetime.now(timezone.utc) + EMAIL_CONFIRMATION_TTL
        )
        background.add_task(send_email_change_email, payload.new_email, raw_token)
    return MessageResponse(message=CHECK_NEW_INBOX)

