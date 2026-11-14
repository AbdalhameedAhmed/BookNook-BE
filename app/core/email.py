"""Transactional email for account confirmation and password reset.

Each email is multipart: a plain-text body plus an HTML alternative rendered from
``email_templates/layout.html`` (the Stitch email design), with the app icon attached
inline as ``cid:rfoof-icon``. No user-supplied content goes into these emails; the only
dynamic value is the link, and every slot is HTML-escaped anyway.

``EMAIL_BACKEND=console`` prints the plain-text version to stdout for local development
instead of sending; never use it in production because the printed links are live credentials.
"""

from __future__ import annotations

import html
import logging
import smtplib
from dataclasses import dataclass
from email.message import EmailMessage
from functools import lru_cache
from pathlib import Path

from core.auth_settings import get_auth_settings

logger = logging.getLogger(__name__)

TEMPLATE_DIR = Path(__file__).parent / "email_templates"
ICON_CID = "rfoof-icon"


@dataclass(frozen=True)
class EmailCopy:
    """Bilingual copy for one email, taken from the Stitch templates."""

    preheader: str
    en_heading: str
    en_body: str
    en_button: str
    en_note: str
    ar_heading: str
    ar_body: str
    ar_button: str
    ar_note: str
    # "muted" for a quiet line, "ochre" for the soft warning box used by password reset.
    note_style: str = "muted"


CONFIRM_COPY = EmailCopy(
    preheader="One click to start sharing books in Fayoum.",
    en_heading="Welcome to Rfoof",
    en_body="Confirm your email address to finish creating your account. This link works for 24 hours.",
    en_button="Confirm my email",
    en_note="If you didn't create an account, you can ignore this email.",
    ar_heading="مرحباً بك في رفوف",
    ar_body="أكّد بريدك الإلكتروني لإكمال إنشاء حسابك. الرابط صالح لمدة ٢٤ ساعة.",
    ar_button="تأكيد بريدي",
    ar_note="إذا لم تقم بإنشاء هذا الحساب، يمكنك تجاهل هذه الرسالة بأمان.",
)

RESET_COPY = EmailCopy(
    preheader="This link expires in 1 hour.",
    en_heading="Reset your password",
    en_body=(
        "Someone asked to reset the password for this Rfoof account. Choose a new password with the "
        "button below. This link works for 1 hour and can be used once."
    ),
    en_button="Choose a new password",
    en_note="If this wasn't you, ignore this email. Your password won't change.",
    ar_heading="إعادة تعيين كلمة المرور",
    ar_body=(
        "طلب أحدهم إعادة تعيين كلمة المرور لهذا الحساب في رفوف. اختر كلمة مرور جديدة من الزر أدناه. "
        "الرابط صالح لمدة ساعة واحدة ويُستخدم مرة واحدة."
    ),
    ar_button="اختيار كلمة مرور جديدة",
    ar_note="إذا لم تكن أنت، تجاهل هذه الرسالة ولن تتغير كلمة المرور.",
    note_style="ochre",
)


@lru_cache(maxsize=1)
def _layout() -> str:
    return (TEMPLATE_DIR / "layout.html").read_text(encoding="utf-8")


@lru_cache(maxsize=1)
def _icon() -> bytes:
    return (TEMPLATE_DIR / "rfoof-icon.png").read_bytes()


def _note_html(text: str, style: str, arabic: bool) -> str:
    font = "Tahoma, Arial, sans-serif" if arabic else "Arial, Tahoma, sans-serif"
    lang = ' lang="ar"' if arabic else ""
    if style == "ochre":
        return (
            '<table role="presentation" border="0" cellpadding="0" cellspacing="0" width="100%"><tr>'
            '<td class="ochre-box" style="background-color: #F7ECD2; border: 1px solid #EBD5A8; '
            'border-radius: 6px; padding: 12px 14px;">'
            f'<p{lang} style="margin: 0; font-family: {font}; font-size: 13px; line-height: 20px; '
            f'color: #7A5306; font-weight: bold;">{html.escape(text)}</p></td></tr></table>'
        )
    return (
        f'<p class="text-muted"{lang} style="margin: 0; font-family: {font}; font-size: 13px; '
        f'line-height: 20px; color: #65756C;">{html.escape(text)}</p>'
    )


def render_email_html(subject: str, copy: EmailCopy, link: str) -> str:
    """Fills the layout. Plain slots are escaped; the two note slots are built from escaped text."""
    values = {
        "subject": subject,
        "preheader": copy.preheader,
        "link": link,
        "en_heading": copy.en_heading,
        "en_body": copy.en_body,
        "en_button": copy.en_button,
        "ar_heading": copy.ar_heading,
        "ar_body": copy.ar_body,
        "ar_button": copy.ar_button,
    }
    rendered = _layout()
    for key, value in values.items():
        rendered = rendered.replace("{{" + key + "}}", html.escape(value, quote=True))
    rendered = rendered.replace("{{en_note}}", _note_html(copy.en_note, copy.note_style, arabic=False))
    return rendered.replace("{{ar_note}}", _note_html(copy.ar_note, copy.note_style, arabic=True))


def send_confirmation_email(to_address: str, raw_token: str) -> None:
    link = f"{get_auth_settings().frontend_url}/confirm-email#token={raw_token}"
    _send(
        to_address,
        "Confirm your Rfoof account | تأكيد حسابك في رفوف",
        CONFIRM_COPY,
        link,
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
        RESET_COPY,
        link,
        "Someone asked to reset the password for this Rfoof account.\n\n"
        f"Choose a new password here (valid for 1 hour):\n{link}\n\n"
        "If this wasn't you, ignore this email; your password will not change.\n\n"
        "---\n\n"
        "طلب أحدهم إعادة تعيين كلمة المرور لهذا الحساب في رفوف.\n\n"
        f"اختر كلمة مرور جديدة من هنا (صالح لمدة ساعة واحدة):\n{link}\n\n"
        "إذا لم تكن أنت، تجاهل هذه الرسالة ولن تتغير كلمة المرور.\n",
    )


def build_message(to_address: str, subject: str, body: str, copy: EmailCopy, link: str, sender: str) -> EmailMessage:
    """multipart/alternative: plain text first, then HTML (multipart/related with the inline icon)."""
    message = EmailMessage()
    message["From"] = sender
    message["To"] = to_address
    message["Subject"] = subject
    message.set_content(body)
    message.add_alternative(render_email_html(subject, copy, link), subtype="html")
    html_part = message.get_payload()[1]
    html_part.add_related(_icon(), maintype="image", subtype="png", cid=f"<{ICON_CID}>", filename="rfoof-icon.png")
    return message


def _send(to_address: str, subject: str, copy: EmailCopy, link: str, body: str) -> None:
    settings = get_auth_settings()

    if settings.email_backend == "console":
        print(f"\n[dev email] To: {to_address}\nSubject: {subject}\n\n{body}", flush=True)
        return

    message = build_message(to_address, subject, body, copy, link, settings.smtp_from or "Rfoof <no-reply@localhost>")
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
