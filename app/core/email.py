"""Transactional email for account confirmation and password reset.

Emails are plain text (no user-supplied HTML). ``EMAIL_BACKEND=console`` prints
messages to stdout for local development instead of sending them; never use it
in production because the printed links are live credentials.
"""

from __future__ import annotations

import logging
import smtplib
from email.message import EmailMessage

from core.auth_settings import get_auth_settings

logger = logging.getLogger(__name__)


def send_confirmation_email(to_address: str, raw_token: str) -> None:
    link = f"{get_auth_settings().frontend_url}/confirm-email#token={raw_token}"
    _send(
        to_address,
        "Confirm your Rfoof account | تأكيد حسابك في رفوف",
        "Welcome to Rfoof!\n\n"
        f"Confirm your email address by opening this link (valid for 24 hours):\n{link}\n\n"
        "If you did not create an account, you can ignore this email.\n\n"
        "---\n\n"
        "مرحباً بك في رفوف!\n\n"
        f"أكّد بريدك الإلكتروني بفتح هذا الرابط (صالح لمدة ٢٤ ساعة):\n{link}\n\n"
        "إذا لم تنشئ حساباً، يمكنك تجاهل هذه الرسالة.\n",
    )


def send_password_reset_email(to_address: str, raw_token: str) -> None:
    link = f"{get_auth_settings().frontend_url}/reset-password#token={raw_token}"
    _send(
        to_address,
        "Reset your Rfoof password | إعادة تعيين كلمة المرور في رفوف",
        "Someone asked to reset the password for this Rfoof account.\n\n"
        f"Choose a new password here (valid for 1 hour):\n{link}\n\n"
        "If this wasn't you, ignore this email; your password will not change.\n\n"
        "---\n\n"
        "طلب أحدهم إعادة تعيين كلمة المرور لهذا الحساب في رفوف.\n\n"
        f"اختر كلمة مرور جديدة من هنا (صالح لمدة ساعة واحدة):\n{link}\n\n"
        "إذا لم تكن أنت، تجاهل هذه الرسالة ولن تتغير كلمة المرور.\n",
    )


def _send(to_address: str, subject: str, body: str) -> None:
    settings = get_auth_settings()
    message = EmailMessage()
    message["From"] = settings.smtp_from or "Rfoof <no-reply@localhost>"
    message["To"] = to_address
    message["Subject"] = subject
    message.set_content(body)

    if settings.email_backend == "console":
        print(f"\n[dev email] To: {to_address}\nSubject: {subject}\n\n{body}", flush=True)
        return

    try:
        smtp_class = smtplib.SMTP_SSL if settings.smtp_security == "ssl" else smtplib.SMTP
        with smtp_class(settings.smtp_host, settings.smtp_port, timeout=15) as smtp:
            if settings.smtp_security == "starttls":
                smtp.starttls()
            if settings.smtp_username:
                smtp.login(settings.smtp_username, settings.smtp_password)
            smtp.send_message(message)
    except (smtplib.SMTPException, OSError) as error:
        # Log the SMTP/network error to aid setup, but never the body: it contains a live token.
        logger.error(
            "Auth email could not be sent via %s:%s: %s: %s",
            settings.smtp_host, settings.smtp_port, type(error).__name__, error,
        )
        return
    logger.warning("Auth email sent via %s (%s)", settings.smtp_host, subject.split(" | ")[0])
