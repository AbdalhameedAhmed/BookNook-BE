"""In-app notifications and Web Push (memory mode).

The memory store mirrors the migration 011 triggers; see supabase/tests/notifications_test.sql.
"""

from types import SimpleNamespace

import pytest
from pywebpush import WebPushException

from core import push
from crud.crud_notifications import MEMORY_NOTIFICATION_STORE
from crud.crud_wallet import MEMORY_WALLET_STORE
from tests.test_deals import User, client

SUBSCRIPTION = {
    "endpoint": "https://push.example.test/sub/abc",
    "keys": {"p256dh": "BPublicKeyValue", "auth": "authsecret"},
    "lang": "en",
}


def notifications(user: User) -> list[dict]:
    response = client.get("/api/v1/me/notifications", headers=user.headers)
    assert response.status_code == 200, response.text
    return response.json()["items"]


def kinds(user: User) -> list[str]:
    return sorted(n["kind"] for n in notifications(user))


def accepted_sale(price: int = 100):
    seller, buyer = User("seller@x.test"), User("buyer@x.test", balance=500)
    listing = seller.list_book(price=price)
    request_id = buyer.request(listing).json()["id"]
    assert seller.decide(request_id, "accepted").status_code == 200
    return seller, buyer, listing, buyer.transaction_for(listing)


@pytest.fixture
def push_on(monkeypatch: pytest.MonkeyPatch) -> list[dict]:
    """Turns push on and records what would be sent instead of calling push services."""
    monkeypatch.setenv("VAPID_PUBLIC_KEY", "BTestPublicKey")
    monkeypatch.setenv("VAPID_PRIVATE_KEY", "test-private-key")
    sent: list[dict] = []
    monkeypatch.setattr(push, "webpush", lambda **kwargs: sent.append(kwargs))
    return sent


# --- Who hears what ---

def test_new_request_notifies_seller_only() -> None:
    seller, buyer = User("seller@x.test"), User("buyer@x.test", balance=500)
    listing = seller.list_book(price=100)

    buyer.request(listing)

    [item] = notifications(seller)
    assert item["kind"] == "request_new"
    assert item["data"] == {"listing_title": "sale book", "actor_name": "Buyer"}
    assert item["read_at"] is None and "pushed_at" not in item
    assert notifications(buyer) == []


def test_accept_notifies_buyer_and_losing_buyers_hear_book_is_gone() -> None:
    seller, buyer, other = User("seller@x.test"), User("buyer@x.test", balance=500), User("other@x.test", balance=500)
    listing = seller.list_book(price=100)
    request_id = buyer.request(listing).json()["id"]
    other.request(listing)

    seller.decide(request_id, "accepted")

    assert kinds(buyer) == ["request_accepted"]
    [lost] = notifications(other)
    assert lost["kind"] == "request_unavailable"
    assert lost["data"]["amount_egp"] == 110
    assert kinds(seller) == ["request_new", "request_new"], "seller is not notified of their own decision"


def test_decline_cancel_and_closed_listing() -> None:
    seller, buyer = User("seller@x.test"), User("buyer@x.test", balance=500)
    listing = seller.list_book(price=50)

    seller.decide(buyer.request(listing).json()["id"], "rejected")
    buyer.decide(buyer.request(listing).json()["id"], "cancelled")
    buyer.request(listing)
    assert client.post(f"/api/v1/listings/{listing}/deactivate", headers=seller.headers).status_code == 200

    assert kinds(buyer) == ["request_rejected", "request_unavailable"]
    assert "request_cancelled" in kinds(seller)


def test_sale_completion_notifies_seller_with_payout() -> None:
    seller, buyer, _, transaction = accepted_sale(price=100)

    assert buyer.confirm(transaction["id"]).status_code == 200

    completed = [n for n in notifications(seller) if n["kind"] == "deal_completed"]
    assert len(completed) == 1 and completed[0]["data"]["amount_egp"] == 90
    assert "deal_completed" not in kinds(buyer)


def test_exchange_confirmations() -> None:
    seller, buyer = User("seller@x.test", balance=50), User("buyer@x.test", balance=50)
    listing = seller.list_book("exchange")
    offer = buyer.list_book("exchange")
    request_id = buyer.request(listing, [offer]).json()["id"]
    seller.decide(request_id, "accepted", offer)
    transaction = buyer.transaction_for(listing)

    buyer.confirm(transaction["id"])
    assert kinds(seller).count("deal_confirmed_by_other") == 1
    seller.confirm(transaction["id"])

    assert kinds(buyer).count("deal_completed") == 1
    assert kinds(seller).count("deal_completed") == 0
    assert kinds(buyer).count("deal_confirmed_by_other") == 0


def test_moderator_cancel_notifies_both_with_refunds() -> None:
    seller, buyer, _, transaction = accepted_sale()
    mod = User("mod@x.test", role="moderator")

    assert client.post(f"/api/v1/admin/transactions/{transaction['id']}/cancel", headers=mod.headers).status_code == 200

    [buyer_note] = [n for n in notifications(buyer) if n["kind"] == "deal_cancelled"]
    assert buyer_note["data"]["amount_egp"] == 110
    [seller_note] = [n for n in notifications(seller) if n["kind"] == "deal_cancelled"]
    assert "amount_egp" not in seller_note["data"], "nothing was held from the seller of a sale"
    assert notifications(mod) == []


def test_topup_review_notifies_owner() -> None:
    owner, mod = User("owner@x.test"), User("mod@x.test", role="moderator")
    row = {"user_id": owner.id, "method": "e_wallet", "sender_number": "01012345678",
           "recipient_number": "01109261212", "receipt_path": "x"}
    approved = MEMORY_WALLET_STORE.create_topup({**row, "amount_egp": 200})
    rejected = MEMORY_WALLET_STORE.create_topup({**row, "amount_egp": 50})

    client.post(f"/api/v1/admin/wallet-topups/{approved['id']}/approve", headers=mod.headers)
    client.post(f"/api/v1/admin/wallet-topups/{rejected['id']}/reject", headers=mod.headers, json={"note": "Blurry"})

    by_kind = {n["kind"]: n["data"] for n in notifications(owner)}
    assert by_kind == {"topup_approved": {"amount_egp": 200}, "topup_rejected": {"amount_egp": 50, "note": "Blurry"}}


# --- Chat ---

def test_chat_messages_collapse_while_unread() -> None:
    seller, buyer, _, transaction = accepted_sale()
    url = f"/api/v1/transactions/{transaction['id']}/messages"
    for body in ("Hello", "When?", "x" * 200):
        assert client.post(url, headers=buyer.headers, json={"body": body}).status_code == 201

    [chat] = [n for n in notifications(seller) if n["kind"] == "message"]
    assert chat["count"] == 3
    assert chat["data"]["preview"] == "x" * 80
    assert chat["data"]["actor_name"] == "Buyer"
    assert not any(n["kind"] == "message" for n in notifications(buyer)), "sender is not notified"

    client.post("/api/v1/me/notifications/read", headers=seller.headers, json={"ids": [chat["id"]]})
    client.post(url, headers=buyer.headers, json={"body": "Still there?"})
    assert [n["count"] for n in notifications(seller) if n["kind"] == "message"] == [1, 3]


# --- Reading and access control ---

def test_unread_count_and_mark_read() -> None:
    seller, buyer = User("seller@x.test"), User("buyer@x.test", balance=500)
    for _ in range(3):
        buyer.request(seller.list_book(price=10))
    assert client.get("/api/v1/me/notifications/unread-count", headers=seller.headers).json() == {"unread_count": 3}

    first = notifications(seller)[0]["id"]
    response = client.post("/api/v1/me/notifications/read", headers=seller.headers, json={"ids": [first]})
    assert response.json() == {"updated": 1, "unread_count": 2}
    response = client.post("/api/v1/me/notifications/read", headers=seller.headers)
    assert response.json() == {"updated": 2, "unread_count": 0}


def test_paging_with_limit_and_before() -> None:
    seller, buyer = User("seller@x.test"), User("buyer@x.test", balance=500)
    for _ in range(5):
        buyer.request(seller.list_book(price=10))

    page = client.get("/api/v1/me/notifications", headers=seller.headers, params={"limit": 2}).json()
    assert len(page["items"]) == 2 and page["unread_count"] == 5
    rest = client.get("/api/v1/me/notifications", headers=seller.headers,
                      params={"limit": 10, "before": page["items"][-1]["created_at"]}).json()["items"]
    assert len(rest) == 3
    assert not {n["id"] for n in rest} & {n["id"] for n in page["items"]}


@pytest.mark.parametrize("method,path", [
    ("GET", "/api/v1/me/notifications"),
    ("GET", "/api/v1/me/notifications/unread-count"),
    ("POST", "/api/v1/me/notifications/read"),
    ("POST", "/api/v1/me/push-subscriptions"),
    ("POST", "/api/v1/me/push-subscriptions/remove"),
])
def test_notification_endpoints_require_auth(method: str, path: str) -> None:
    assert client.request(method, path, json={}).status_code == 401


def test_users_cannot_see_or_mark_others_notifications() -> None:
    seller, buyer, stranger = User("seller@x.test"), User("buyer@x.test", balance=500), User("s@x.test")
    buyer.request(seller.list_book(price=10))
    seller_note = notifications(seller)[0]["id"]

    assert notifications(stranger) == []
    response = client.post("/api/v1/me/notifications/read", headers=stranger.headers, json={"ids": [seller_note]})
    assert response.json()["updated"] == 0
    assert notifications(seller)[0]["read_at"] is None


def test_mark_read_rejects_bad_ids() -> None:
    user = User("u@x.test")
    assert client.post("/api/v1/me/notifications/read", headers=user.headers, json={"ids": ["nope"]}).status_code == 422


# --- Push subscriptions ---

def test_push_is_off_without_vapid_keys() -> None:
    user = User("u@x.test")
    assert client.get("/api/v1/push/public-key").status_code == 404
    assert client.post("/api/v1/me/push-subscriptions", headers=user.headers, json=SUBSCRIPTION).status_code == 404
    assert push.dispatch_pending_notifications() == 0


def test_public_key_is_served_when_configured(push_on) -> None:
    assert client.get("/api/v1/push/public-key").json() == {"public_key": "BTestPublicKey"}


@pytest.mark.parametrize("change", [
    {"endpoint": "http://push.example.test/insecure"},
    {"endpoint": "https://push.example.test/" + "x" * 1000},
    {"keys": {"p256dh": "x" * 201, "auth": "a"}},
    {"keys": {"p256dh": "k", "auth": ""}},
    {"lang": "fr"},
])
def test_invalid_subscriptions_are_rejected(push_on, change: dict) -> None:
    user = User("u@x.test")
    response = client.post("/api/v1/me/push-subscriptions", headers=user.headers, json={**SUBSCRIPTION, **change})
    assert response.status_code == 422
    assert MEMORY_NOTIFICATION_STORE.subscriptions == {}


def test_subscription_is_capped_and_removable_only_by_owner(push_on) -> None:
    user, other = User("u@x.test"), User("o@x.test")
    for i in range(12):
        body = {**SUBSCRIPTION, "endpoint": f"https://push.example.test/{i}"}
        assert client.post("/api/v1/me/push-subscriptions", headers=user.headers, json=body).status_code == 204
    assert len(MEMORY_NOTIFICATION_STORE.subscriptions) == 10

    target = {"endpoint": "https://push.example.test/11"}
    client.post("/api/v1/me/push-subscriptions/remove", headers=other.headers, json=target)
    assert target["endpoint"] in MEMORY_NOTIFICATION_STORE.subscriptions
    client.post("/api/v1/me/push-subscriptions/remove", headers=user.headers, json=target)
    assert target["endpoint"] not in MEMORY_NOTIFICATION_STORE.subscriptions


# --- Push delivery ---

def test_request_pushes_to_sellers_browser_in_their_language(push_on) -> None:
    seller, buyer = User("seller@x.test"), User("buyer@x.test", balance=500)
    client.post("/api/v1/me/push-subscriptions", headers=seller.headers, json={**SUBSCRIPTION, "lang": "ar"})

    buyer.request(seller.list_book(price=100))

    [call] = push_on
    assert call["subscription_info"] == {"endpoint": SUBSCRIPTION["endpoint"], "keys": SUBSCRIPTION["keys"]}
    assert call["vapid_private_key"] == "test-private-key"
    assert '"title": "طلب جديد"' in call["data"]
    assert "Buyer" in call["data"] and "/account/requests" in call["data"]
    assert push.dispatch_pending_notifications() == 0, "already-pushed notifications are not sent again"


def test_each_chat_message_repushes_the_collapsed_thread(push_on) -> None:
    seller, buyer, _, transaction = accepted_sale()
    client.post("/api/v1/me/push-subscriptions", headers=seller.headers, json=SUBSCRIPTION)
    push_on.clear()

    for body in ("one", "two"):
        client.post(f"/api/v1/transactions/{transaction['id']}/messages", headers=buyer.headers, json={"body": body})

    assert len(push_on) == 2
    assert f'"tag": "tx-{transaction["id"]}"' in push_on[1]["data"]
    assert '"body": "(2) two"' in push_on[1]["data"]


def test_gone_subscription_is_deleted(monkeypatch: pytest.MonkeyPatch, push_on) -> None:
    seller, buyer = User("seller@x.test"), User("buyer@x.test", balance=500)
    client.post("/api/v1/me/push-subscriptions", headers=seller.headers, json=SUBSCRIPTION)

    def gone(**_: object) -> None:
        raise WebPushException("gone", response=SimpleNamespace(status_code=410))
    monkeypatch.setattr(push, "webpush", gone)

    buyer.request(seller.list_book(price=10))

    assert MEMORY_NOTIFICATION_STORE.subscriptions == {}
    assert kinds(seller) == ["request_new"], "the in-app notification is unaffected"


def test_push_failure_does_not_break_the_request(monkeypatch: pytest.MonkeyPatch, push_on) -> None:
    seller, buyer = User("seller@x.test"), User("buyer@x.test", balance=500)
    client.post("/api/v1/me/push-subscriptions", headers=seller.headers, json=SUBSCRIPTION)
    monkeypatch.setattr(push, "webpush", lambda **_: (_ for _ in ()).throw(RuntimeError("network down")))

    assert buyer.request(seller.list_book(price=10)).status_code == 201
    assert SUBSCRIPTION["endpoint"] in MEMORY_NOTIFICATION_STORE.subscriptions


def test_payload_text_treats_user_values_as_plain_text() -> None:
    payload = push.build_payload(
        {"id": "n1", "kind": "request_new", "transaction_id": None,
         "data": {"actor_name": "{title} <b>x</b>", "listing_title": "Dune"}}, "en")
    assert payload["body"] == "{title} <b>x</b> wants “Dune”"
    assert payload["tag"] == "n-n1"


# --- Supabase mode: thin RPC wrappers ---

def test_supabase_mode_calls_notification_rpcs(monkeypatch: pytest.MonkeyPatch) -> None:
    import httpx

    from core.auth_settings import get_auth_settings
    from supabase_service import get_supabase_service
    from tests.test_supabase import sign_in_as

    user_id = "b797b8b4-09c5-4f7f-90d4-e89a595c6c4b"
    monkeypatch.setenv("RFOOF_STORAGE", "supabase")
    monkeypatch.setenv("SUPABASE_URL", "https://project.example")
    monkeypatch.setenv("SUPABASE_SECRET_KEY", "sb_secret_test-key")
    monkeypatch.setenv("AUTH_JWT_SECRET", "test-secret-" + "x" * 40)
    get_supabase_service.cache_clear()
    get_auth_settings.cache_clear()
    headers = sign_in_as(monkeypatch, user_id)
    calls: list[tuple[str, dict]] = []
    results = {"my_notifications": {"items": [], "unread_count": 4}, "mark_notifications_read": 2,
               "unread_notification_count": 2}

    def fake_request(method: str, url: str, *, json: dict, **_: object) -> httpx.Response:
        name = url.rsplit("/", 1)[-1]
        calls.append((name, json))
        return httpx.Response(200, json=results.get(name)) if name in results else httpx.Response(204)

    monkeypatch.setattr(httpx, "request", fake_request)
    try:
        page = client.get("/api/v1/me/notifications", headers=headers,
                          params={"limit": 5, "before": "2026-11-20T10:00:00+00:00"}).json()
        read = client.post("/api/v1/me/notifications/read", headers=headers,
                           json={"ids": ["f1b0ab0f-fdbc-4d7b-8e1b-e169cb7e3972"]}).json()
        client.post("/api/v1/me/push-subscriptions/remove", headers=headers, json={"endpoint": "https://p.example/x"})
    finally:
        get_supabase_service.cache_clear()
        get_auth_settings.cache_clear()

    assert page == {"items": [], "unread_count": 4}
    assert read == {"updated": 2, "unread_count": 2}
    assert calls == [
        ("my_notifications", {"p_user_id": user_id, "p_limit": 5, "p_before": "2026-11-20T10:00:00+00:00"}),
        ("mark_notifications_read", {"p_user_id": user_id, "p_ids": ["f1b0ab0f-fdbc-4d7b-8e1b-e169cb7e3972"]}),
        ("unread_notification_count", {"p_user_id": user_id}),
        ("delete_push_subscription", {"p_endpoint": "https://p.example/x", "p_user_id": user_id}),
    ]


# --- Moderator alerts (migration 012) ---

def test_new_topup_alerts_moderators_but_not_the_submitter() -> None:
    owner, mod = User("owner@x.test"), User("mod@x.test", role="moderator")
    other_mod = User("mod2@x.test", role="moderator")
    MEMORY_WALLET_STORE.create_topup({"user_id": owner.id, "method": "e_wallet", "sender_number": "01012345678",
                                      "recipient_number": "01109261212", "receipt_path": "x", "amount_egp": 200})

    for moderator in (mod, other_mod):
        [item] = notifications(moderator)
        assert item["kind"] == "topup_submitted" and item["data"] == {"actor_name": "Owner", "amount_egp": 200}
    assert notifications(owner) == []


def test_new_report_alerts_moderators() -> None:
    seller, reporter, mod = User("seller@x.test"), User("reporter@x.test"), User("mod@x.test", role="moderator")
    listing = seller.list_book(price=100)

    response = client.post("/api/v1/reports", headers=reporter.headers,
                           json={"listing_id": listing, "reason": "Fake listing"})

    assert response.status_code == 201, response.text
    [item] = notifications(mod)
    assert item["kind"] == "report_new" and item["listing_id"] == listing
    assert item["data"] == {"actor_name": "Reporter", "listing_title": "sale book", "preview": "Fake listing"}
    assert notifications(seller) == [] and notifications(reporter) == []


# --- Email for request updates only ---

@pytest.fixture
def emails_on(monkeypatch: pytest.MonkeyPatch) -> list:
    """Turns notification emails on and records them instead of sending."""
    monkeypatch.setenv("NOTIFICATION_EMAILS", "on")
    sent: list = []
    monkeypatch.setattr(push, "send_notification_emails", lambda emails: sent.extend(emails))
    return sent


def confirmed(user: User) -> User:
    from datetime import datetime, timezone

    from crud.crud_user import MEMORY_AUTH_STORE
    MEMORY_AUTH_STORE.users[user.id]["email_confirmed_at"] = datetime.now(timezone.utc)
    return user


def test_only_request_updates_are_emailed(emails_on) -> None:
    seller, buyer = confirmed(User("seller@x.test")), confirmed(User("buyer@x.test", balance=500))
    listing = seller.list_book(price=100)
    request_id = buyer.request(listing).json()["id"]
    seller.decide(request_id, "accepted")
    transaction = buyer.transaction_for(listing)
    client.post(f"/api/v1/handovers/{transaction['id']}/messages", headers=buyer.headers, json={"body": "Hi"})
    buyer.confirm(transaction["id"])

    assert [(e.to_address, e.en_title) for e in emails_on] == [
        ("seller@x.test", "New request"),
        ("buyer@x.test", "Request accepted"),
    ], "chat messages and handover progress are never emailed"
    assert "deal_completed" in kinds(seller), "completion is still notified in-app"
    assert emails_on[0].path == "/account/requests" and "Buyer" in emails_on[0].en_body
    assert emails_on[0].ar_title == "طلب جديد"


def test_wallet_and_moderator_alerts_are_not_emailed(emails_on) -> None:
    owner, mod = confirmed(User("owner@x.test")), confirmed(User("mod@x.test", role="moderator"))
    topup = MEMORY_WALLET_STORE.create_topup({"user_id": owner.id, "method": "e_wallet", "sender_number": "01012345678",
                                              "recipient_number": "01109261212", "receipt_path": "x", "amount_egp": 200})
    client.post(f"/api/v1/admin/wallet-topups/{topup['id']}/approve", headers=mod.headers)

    assert kinds(owner) == ["topup_approved"] and kinds(mod) == ["topup_submitted"]
    assert emails_on == []


def test_unconfirmed_addresses_and_switched_off_emails_get_nothing(emails_on, monkeypatch: pytest.MonkeyPatch) -> None:
    seller, buyer = User("seller@x.test"), User("buyer@x.test", balance=500)
    buyer.request(seller.list_book(price=100))
    assert emails_on == [], "the seller never confirmed their email"

    monkeypatch.setenv("NOTIFICATION_EMAILS", "off")
    confirmed(seller)
    buyer2 = User("buyer2@x.test", balance=500)
    buyer2.request(seller.list_book(price=50))
    assert emails_on == []


def test_notification_email_escapes_user_text_and_keeps_it_out_of_the_subject() -> None:
    from core.email import notification_message

    payload = push.build_email({"id": "n1", "kind": "request_new", "transaction_id": None, "email": "s@x.test",
                                "data": {"actor_name": "<script>x</script>", "listing_title": "Dune\r\nBcc: a@b.c"}})
    message, body = notification_message(payload, "Rfoof <no-reply@x.test>")

    assert message["Subject"] == "Rfoof: New request | طلب جديد"
    assert message["To"] == "s@x.test" and message["Bcc"] is None
    html_part = message.get_payload()[1].get_payload()[0].get_content()
    assert "<script>" not in html_part and "&lt;script&gt;" in html_part
    assert "http://localhost:5173/account/requests" in body


def test_notification_emails_share_one_smtp_connection(monkeypatch: pytest.MonkeyPatch) -> None:
    from core import email as email_module
    from core.auth_settings import get_auth_settings

    monkeypatch.setenv("EMAIL_BACKEND", "smtp")
    monkeypatch.setenv("SMTP_HOST", "smtp.example.test")
    monkeypatch.setenv("SMTP_FROM", "Rfoof <no-reply@example.test>")
    monkeypatch.setenv("SMTP_USERNAME", "")
    monkeypatch.setenv("SMTP_SECURITY", "starttls")
    monkeypatch.setenv("SMTP_PORT", "587")
    get_auth_settings.cache_clear()
    connections: list[list] = []

    class FakeSMTP:
        def __init__(self, host: str, port: int, timeout: int) -> None:
            self.sent: list = []
            connections.append(self.sent)
        def __enter__(self): return self
        def __exit__(self, *args): return False
        def starttls(self) -> None: pass
        def send_message(self, message) -> None: self.sent.append(message["To"])

    monkeypatch.setattr(email_module.smtplib, "SMTP", FakeSMTP)
    emails = [push.build_email({"id": str(i), "kind": "deal_completed", "transaction_id": "t", "email": f"u{i}@x.test",
                                "data": {"listing_title": "Dune"}}) for i in range(3)]
    email_module.send_notification_emails(emails)
    get_auth_settings.cache_clear()

    assert connections == [["u0@x.test", "u1@x.test", "u2@x.test"]]
