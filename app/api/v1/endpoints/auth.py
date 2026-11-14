"""Email + password authentication owned by the backend.

Endpoints that send email always answer with the same generic message so
they cannot be used to discover which emails are registered.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, status

from api.deps import get_authenticated_user
from core import rate_limit
from core.auth_settings import EMAIL_CONFIRMATION_TTL, PASSWORD_RESET_TTL
from core.email import send_confirmation_email, send_password_reset_email
from core.security import (
    create_access_token,
    hash_one_time_token,
    hash_password,
    new_one_time_token,
    verify_password,
)
from crud.crud_user import get_auth_store
from schemas.auth import (
    AuthUser,
    EmailRequest,
    LoginRequest,
    MessageResponse,
    ResetPasswordRequest,
    SessionResponse,
    SignUpRequest,
    TokenRequest,
)

router = APIRouter(prefix="/auth", tags=["auth"])

CHECK_INBOX = "If the email can receive messages, a link is on its way. Check your inbox."
INVALID_LINK = "Invalid or expired link"


@router.post("/signup", status_code=status.HTTP_202_ACCEPTED, response_model=MessageResponse)
def sign_up(payload: SignUpRequest, request: Request, background: BackgroundTasks) -> MessageResponse:
    _limit(request, payload.email, rate_limit.email_send_by_email)
    store = get_auth_store()
    password_hash = hash_password(payload.password)
    user = store.register_user(payload.email, password_hash, payload.display_name)
    if user is None:
        # Already registered: help an unconfirmed owner finish sign-up, otherwise say nothing.
        user = store.find_user_by_email(payload.email)
        if user is None or user.get("email_confirmed_at"):
            return MessageResponse(message=CHECK_INBOX)
    _send_confirmation(store, user, background)
    return MessageResponse(message=CHECK_INBOX)


@router.post("/login", response_model=SessionResponse)
def log_in(payload: LoginRequest, request: Request) -> SessionResponse:
    _limit(request, payload.email, rate_limit.login_by_email)
    store = get_auth_store()
    user = store.find_user_by_email(payload.email)
    if not verify_password(payload.password, user.get("password_hash") if user else None):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid login credentials")
    if not user.get("email_confirmed_at"):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Email not confirmed")
    return _session_for(store, user)


@router.post("/confirm-email", response_model=SessionResponse)
def confirm_email(payload: TokenRequest, request: Request) -> SessionResponse:
    _limit(request, None)
    store = get_auth_store()
    user_id = store.confirm_user_email(hash_one_time_token(payload.token))
    user = store.get_user(user_id) if user_id else None
    if user is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=INVALID_LINK)
    return _session_for(store, user)


@router.post("/resend-confirmation", status_code=status.HTTP_202_ACCEPTED, response_model=MessageResponse)
def resend_confirmation(payload: EmailRequest, request: Request, background: BackgroundTasks) -> MessageResponse:
    _limit(request, payload.email, rate_limit.email_send_by_email)
    store = get_auth_store()
    user = store.find_user_by_email(payload.email)
    if user is not None and not user.get("email_confirmed_at"):
        _send_confirmation(store, user, background)
    return MessageResponse(message=CHECK_INBOX)


@router.post("/forgot-password", status_code=status.HTTP_202_ACCEPTED, response_model=MessageResponse)
def forgot_password(payload: EmailRequest, request: Request, background: BackgroundTasks) -> MessageResponse:
    _limit(request, payload.email, rate_limit.email_send_by_email)
    store = get_auth_store()
    user = store.find_user_by_email(payload.email)
    if user is not None:
        raw_token, token_hash = new_one_time_token()
        store.issue_auth_token(str(user["id"]), "password_reset", token_hash, _expires_in(PASSWORD_RESET_TTL))
        background.add_task(send_password_reset_email, user["email"], raw_token)
    return MessageResponse(message=CHECK_INBOX)


@router.post("/reset-password", response_model=SessionResponse)
def reset_password(payload: ResetPasswordRequest, request: Request) -> SessionResponse:
    _limit(request, None)
    store = get_auth_store()
    user_id = store.reset_user_password(hash_one_time_token(payload.token), hash_password(payload.password))
    user = store.get_user(user_id) if user_id else None
    if user is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=INVALID_LINK)
    return _session_for(store, user)


@router.get("/me", response_model=AuthUser)
def read_current_user(current_user: dict[str, object] = Depends(get_authenticated_user)) -> AuthUser:
    store = get_auth_store()
    user_id = str(current_user["id"])
    return AuthUser(
        id=user_id,
        email=str(current_user["email"]),
        display_name=store.get_display_name(user_id) or _fallback_name(str(current_user["email"])),
        role=current_user["role"],
    )


def _session_for(store: Any, user: dict[str, Any]) -> SessionResponse:
    user_id = str(user["id"])
    access_token, expires_in = create_access_token(user_id)
    return SessionResponse(
        access_token=access_token,
        expires_in=expires_in,
        user=AuthUser(
            id=user_id,
            email=user["email"],
            display_name=store.get_display_name(user_id) or _fallback_name(user["email"]),
            role=user.get("role") or "user",
        ),
    )


def _send_confirmation(store: Any, user: dict[str, Any], background: BackgroundTasks) -> None:
    raw_token, token_hash = new_one_time_token()
    store.issue_auth_token(str(user["id"]), "email_confirmation", token_hash, _expires_in(EMAIL_CONFIRMATION_TTL))
    background.add_task(send_confirmation_email, user["email"], raw_token)


def _limit(request: Request, email: str | None, per_email: rate_limit.RateLimiter | None = None) -> None:
    client_ip = request.client.host if request.client else "unknown"
    rate_limit.auth_by_ip.hit(client_ip)
    if per_email is not None and email:
        per_email.hit(email)


def _expires_in(ttl: Any) -> datetime:
    return datetime.now(timezone.utc) + ttl


def _fallback_name(email: str) -> str:
    return email.split("@", 1)[0] or "Rfoof user"
