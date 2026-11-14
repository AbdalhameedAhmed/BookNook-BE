"""Authentication settings read from the environment.

In ``memory`` storage mode (tests/demo) the settings fall back to safe local
defaults. In ``supabase`` mode a real JWT secret and email configuration are
required so misconfiguration fails loudly instead of silently.
"""

from __future__ import annotations

import os
import secrets
from dataclasses import dataclass
from datetime import timedelta
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[2] / ".env", override=False)

MIN_JWT_SECRET_LENGTH = 32
EMAIL_CONFIRMATION_TTL = timedelta(hours=24)
PASSWORD_RESET_TTL = timedelta(hours=1)


@dataclass(frozen=True)
class AuthSettings:
    jwt_secret: str
    access_token_ttl: timedelta
    frontend_url: str
    email_backend: str
    smtp_host: str
    smtp_port: int
    smtp_username: str
    smtp_password: str
    smtp_from: str
    smtp_security: str


@lru_cache(maxsize=1)
def get_auth_settings() -> AuthSettings:
    is_memory = os.getenv("RFOOF_STORAGE", "memory").strip().lower() == "memory"

    jwt_secret = os.getenv("AUTH_JWT_SECRET", "").strip()
    if not jwt_secret:
        if not is_memory:
            raise RuntimeError("AUTH_JWT_SECRET is required (generate one with: openssl rand -hex 32)")
        jwt_secret = secrets.token_hex(32)  # Demo mode only: tokens reset on every restart.
    if len(jwt_secret) < MIN_JWT_SECRET_LENGTH:
        raise RuntimeError(f"AUTH_JWT_SECRET must be at least {MIN_JWT_SECRET_LENGTH} characters")

    ttl_minutes = int(os.getenv("AUTH_ACCESS_TOKEN_TTL_MINUTES", str(7 * 24 * 60)))
    if ttl_minutes < 5:
        raise RuntimeError("AUTH_ACCESS_TOKEN_TTL_MINUTES must be at least 5")

    email_backend = os.getenv("EMAIL_BACKEND", "console" if is_memory else "smtp").strip().lower()
    if email_backend not in {"smtp", "console"}:
        raise RuntimeError("EMAIL_BACKEND must be either 'smtp' or 'console'")

    smtp_security = os.getenv("SMTP_SECURITY", "starttls").strip().lower()
    if smtp_security not in {"starttls", "ssl", "none"}:
        raise RuntimeError("SMTP_SECURITY must be 'starttls', 'ssl', or 'none'")

    settings = AuthSettings(
        jwt_secret=jwt_secret,
        access_token_ttl=timedelta(minutes=ttl_minutes),
        frontend_url=os.getenv("FRONTEND_URL", "http://localhost:5173").strip().rstrip("/"),
        email_backend=email_backend,
        smtp_host=os.getenv("SMTP_HOST", "").strip(),
        smtp_port=int(os.getenv("SMTP_PORT", "587")),
        smtp_username=os.getenv("SMTP_USERNAME", "").strip(),
        smtp_password=os.getenv("SMTP_PASSWORD", ""),
        smtp_from=os.getenv("SMTP_FROM", "").strip(),
        smtp_security=smtp_security,
    )
    if settings.email_backend == "smtp" and not (settings.smtp_host and settings.smtp_from):
        raise RuntimeError("EMAIL_BACKEND=smtp requires SMTP_HOST and SMTP_FROM")
    return settings
