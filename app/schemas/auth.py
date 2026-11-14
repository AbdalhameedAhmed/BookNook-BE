from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, EmailStr, Field, field_validator

from core.security import MAX_PASSWORD_BYTES

MIN_PASSWORD_LENGTH = 8


def _normalize_email(value: str) -> str:
    return value.strip().lower()


def _validate_new_password(value: str) -> str:
    if len(value) < MIN_PASSWORD_LENGTH:
        raise ValueError(f"Password must be at least {MIN_PASSWORD_LENGTH} characters")
    if len(value.encode("utf-8")) > MAX_PASSWORD_BYTES:
        raise ValueError("Password is too long")
    return value


class SignUpRequest(BaseModel):
    email: EmailStr
    password: str
    display_name: str = Field(..., min_length=1, max_length=80)

    _email = field_validator("email")(_normalize_email)
    _password = field_validator("password")(_validate_new_password)

    @field_validator("display_name")
    @classmethod
    def _strip_display_name(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Display name is required")
        return value


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(..., min_length=1, max_length=200)

    _email = field_validator("email")(_normalize_email)


class EmailRequest(BaseModel):
    email: EmailStr

    _email = field_validator("email")(_normalize_email)


class TokenRequest(BaseModel):
    token: str = Field(..., min_length=20, max_length=200)


class ResetPasswordRequest(TokenRequest):
    password: str

    _password = field_validator("password")(_validate_new_password)


class AuthUser(BaseModel):
    id: str
    email: str
    display_name: str
    role: Literal["user", "moderator"]


class SessionResponse(BaseModel):
    access_token: str
    token_type: Literal["bearer"] = "bearer"
    expires_in: int
    user: AuthUser


class MessageResponse(BaseModel):
    message: str
