"""Web Push delivery for notifications.

Endpoints that change deal or wallet state call ``dispatch_pending_pushes`` before responding.
This is deliberate: on serverless hosts (Vercel) work scheduled after the response, such as FastAPI
background tasks, is not guaranteed to run. It claims notifications that have not been pushed yet
(so concurrent requests never send the same one twice) and sends each to the user's subscribed
browsers, with a short timeout so a slow push service delays the response by seconds at most.
Without VAPID keys configured, push is switched off and notifications are in-app only.

Generate keys with ``python scripts/generate_vapid_keys.py``.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any

from pywebpush import WebPushException, webpush

from crud.crud_notifications import get_notification_store

logger = logging.getLogger(__name__)

# Small batches keep the added response time bounded; leftovers go out with the next action.
CLAIM_BATCH = 20
PUSH_TIMEOUT_SECONDS = 4
PUSH_TTL_SECONDS = 24 * 60 * 60

# Short texts shown by the phone/desktop notification. Keep in step with Rfoof-FE/src/messages/notifications.ts.
TEXTS: dict[str, dict[str, tuple[str, str]]] = {
    "en": {
        "request_new": ("New request", "{actor} wants “{title}”"),
        "request_accepted": ("Request accepted", "{actor} accepted your request for “{title}”. Arrange the handover."),
        "request_rejected": ("Request declined", "Your request for “{title}” was declined.{refund}"),
        "request_unavailable": ("Book no longer available", "“{title}” is no longer available.{refund}"),
        "request_cancelled": ("Request cancelled", "{actor} cancelled their request for “{title}”."),
        "deal_confirmed_by_other": ("Handover confirmed", "{actor} confirmed the handover of “{title}”. Confirm yours too."),
        "deal_completed": ("Deal completed", "The handover of “{title}” is complete.{payout}"),
        "deal_cancelled": ("Handover cancelled", "The handover of “{title}” was cancelled.{refund}"),
        "message": ("{actor}", "{preview}"),
        "topup_approved": ("Top-up approved", "{amount} EGP was added to your wallet."),
        "topup_rejected": ("Top-up rejected", "Your top-up of {amount} EGP was not approved."),
    },
    "ar": {
        "request_new": ("طلب جديد", "{actor} يريد «{title}»"),
        "request_accepted": ("تم قبول طلبك", "قبل {actor} طلبك على «{title}». رتّب موعد التسليم."),
        "request_rejected": ("تم رفض الطلب", "تم رفض طلبك على «{title}».{refund}"),
        "request_unavailable": ("الكتاب لم يعد متاحًا", "«{title}» لم يعد متاحًا.{refund}"),
        "request_cancelled": ("تم إلغاء الطلب", "ألغى {actor} طلبه على «{title}»."),
        "deal_confirmed_by_other": ("تأكيد التسليم", "أكّد {actor} تسليم «{title}». أكّد أنت أيضًا."),
        "deal_completed": ("اكتملت الصفقة", "اكتمل تسليم «{title}».{payout}"),
        "deal_cancelled": ("تم إلغاء التسليم", "تم إلغاء تسليم «{title}».{refund}"),
        "message": ("{actor}", "{preview}"),
        "topup_approved": ("تم قبول الشحن", "تمت إضافة {amount} ج.م إلى محفظتك."),
        "topup_rejected": ("تم رفض الشحن", "لم تتم الموافقة على شحن {amount} ج.م."),
    },
}
MONEY_SUFFIX = {
    "en": {"refund": " {amount} EGP was returned to your wallet.", "payout": " {amount} EGP was added to your wallet."},
    "ar": {"refund": " تمت إعادة {amount} ج.م إلى محفظتك.", "payout": " تمت إضافة {amount} ج.م إلى محفظتك."},
}
FALLBACK_NAME = {"en": "Someone", "ar": "مستخدم"}

LINKS = {
    "request_new": "/account/requests",
    "request_rejected": "/account/requests",
    "request_unavailable": "/account/requests",
    "request_cancelled": "/account/requests",
    "request_accepted": "/account/handovers",
    "deal_confirmed_by_other": "/account/handovers",
    "deal_completed": "/account/handovers",
    "deal_cancelled": "/account/handovers",
    "message": "/account/handovers",
    "topup_approved": "/account/wallet",
    "topup_rejected": "/account/wallet",
}


def vapid_public_key() -> str | None:
    return os.getenv("VAPID_PUBLIC_KEY", "").strip() or None


def push_enabled() -> bool:
    return bool(vapid_public_key() and os.getenv("VAPID_PRIVATE_KEY", "").strip())


def link_for(kind: str) -> str:
    return LINKS.get(kind, "/account/notifications")


def build_payload(notification: dict[str, Any], lang: str) -> dict[str, Any]:
    """Title/body text for one notification. User-supplied values are inserted as plain text."""
    lang = lang if lang in TEXTS else "en"
    kind = str(notification["kind"])
    data = notification.get("data") or {}
    amount = data.get("amount_egp")
    suffix = MONEY_SUFFIX[lang]
    values = {
        "actor": data.get("actor_name") or FALLBACK_NAME[lang],
        "title": data.get("listing_title") or "",
        "preview": data.get("preview") or "",
        "amount": amount or 0,
        "refund": suffix["refund"].format(amount=amount) if amount else "",
        "payout": suffix["payout"].format(amount=amount) if amount else "",
    }
    title, body = TEXTS[lang].get(kind, ("Rfoof", ""))
    count = int(notification.get("count") or 1)
    if kind == "message" and count > 1:
        body = f"({count}) {body}"
    return {
        "title": title.format(**values),
        "body": body.format(**values),
        "url": link_for(kind),
        # One alert per handover thread / request on the device: newer pushes replace older ones.
        "tag": f"tx-{notification['transaction_id']}" if notification.get("transaction_id")
        else f"n-{notification['id']}",
        "kind": kind,
    }


def dispatch_pending_pushes() -> int:
    """Sends every unpushed notification to its owner's browsers. Returns the number of pushes sent."""
    if not push_enabled():
        return 0
    store = get_notification_store()
    sent = 0
    try:
        claimed = store.claim_unpushed(CLAIM_BATCH)
    except Exception:  # noqa: BLE001 - background task: never crash the worker
        logger.exception("Could not claim notifications for push")
        return 0
    for notification in claimed:
        for subscription in notification.get("subscriptions") or []:
            if _send(store, subscription, build_payload(notification, subscription.get("lang", "en"))):
                sent += 1
    return sent


def _send(store: Any, subscription: dict[str, Any], payload: dict[str, Any]) -> bool:
    try:
        webpush(
            subscription_info={
                "endpoint": subscription["endpoint"],
                "keys": {"p256dh": subscription["p256dh"], "auth": subscription["auth"]},
            },
            data=json.dumps(payload, ensure_ascii=False),
            vapid_private_key=os.environ["VAPID_PRIVATE_KEY"].strip(),
            vapid_claims={"sub": os.getenv("VAPID_SUBJECT", "").strip() or "mailto:admin@example.com"},
            ttl=PUSH_TTL_SECONDS,
            timeout=PUSH_TIMEOUT_SECONDS,
        )
        return True
    except WebPushException as error:
        status = getattr(error.response, "status_code", None)
        if status in (404, 410):
            # The browser unsubscribed or the subscription expired.
            store.delete_subscription(subscription["endpoint"])
        else:
            # Do not log the endpoint: it is a bearer capability for this user's browser.
            logger.warning("Web push failed with status %s", status)
        return False
    except Exception:  # noqa: BLE001
        logger.warning("Web push failed", exc_info=False)
        return False
