import httpx
import pytest
from fastapi.testclient import TestClient

from app import main
from core.auth_settings import get_auth_settings
from core.config import MAX_RECEIPT_SIZE_BYTES
from core.security import create_access_token
from crud.crud_user import MEMORY_AUTH_STORE
from supabase_service import SupabaseService, get_supabase_service

client = TestClient(main.app)

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
JPEG = b"\xff\xd8\xff" + b"\x00" * 64


def make_user(email: str, role: str = "user") -> dict[str, str]:
    user = MEMORY_AUTH_STORE.register_user(email, "unused-hash", email.split("@")[0])
    MEMORY_AUTH_STORE.users[user["id"]]["role"] = role
    token, _ = create_access_token(user["id"])
    return {"Authorization": f"Bearer {token}"}


def submit_topup(headers: dict[str, str], *, sender: str = "01012345678", amount: str = "200",
                 receipt: bytes = PNG, content_type: str = "image/png") -> httpx.Response:
    return client.post(
        "/api/v1/wallet/topups",
        data={"method": "e_wallet", "sender_number": sender, "amount_egp": amount},
        files={"receipt": ("receipt.png", receipt, content_type)},
        headers=headers,
    )


@pytest.fixture
def reader() -> dict[str, str]:
    return make_user("reader@example.com")


@pytest.fixture
def moderator() -> dict[str, str]:
    return make_user("mod@example.com", role="moderator")


# --- Authentication and authorization boundaries ---

def test_wallet_requires_authentication() -> None:
    assert client.get("/api/v1/wallet").status_code == 401
    assert submit_topup({}).status_code == 401


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("GET", "/api/v1/admin/overview"),
        ("GET", "/api/v1/admin/wallet-topups"),
        ("POST", "/api/v1/admin/wallet-topups/00000000-0000-0000-0000-000000000000/approve"),
        ("POST", "/api/v1/admin/wallet-topups/00000000-0000-0000-0000-000000000000/reject"),
    ],
)
def test_admin_endpoints_reject_anonymous_and_regular_users(reader, method: str, path: str) -> None:
    assert client.request(method, path).status_code == 401
    assert client.request(method, path, headers=reader).status_code == 403


def test_regular_user_cannot_approve_their_own_topup(reader) -> None:
    topup_id = submit_topup(reader).json()["id"]

    response = client.post(f"/api/v1/admin/wallet-topups/{topup_id}/approve", headers=reader)

    assert response.status_code == 403
    assert client.get("/api/v1/wallet", headers=reader).json()["balance_egp"] == 0


def test_user_sees_only_their_own_topups(reader) -> None:
    other = make_user("other@example.com")
    submit_topup(other)

    wallet = client.get("/api/v1/wallet", headers=reader).json()

    assert wallet["topups"] == []


# --- Wallet page and submissions ---

def test_new_wallet_has_zero_balance_and_shows_ewallet_number(reader) -> None:
    response = client.get("/api/v1/wallet", headers=reader)

    assert response.status_code == 200
    assert response.json()["balance_egp"] == 0
    assert response.json()["options"] == [{"method": "e_wallet", "recipient_number": "01109261212"}]


def test_topup_is_created_pending_without_changing_balance(reader) -> None:
    response = submit_topup(reader, sender="+20 101 234 5678")

    assert response.status_code == 201
    topup = response.json()
    assert topup["status"] == "pending"
    assert topup["sender_number"] == "01012345678"
    assert topup["recipient_number"] == "01109261212"
    assert topup["amount_egp"] == 200
    assert "receipt_path" not in topup and "reviewed_by" not in topup

    wallet = client.get("/api/v1/wallet", headers=reader).json()
    assert wallet["balance_egp"] == 0
    assert [t["id"] for t in wallet["topups"]] == [topup["id"]]


@pytest.mark.parametrize("sender", ["0101234567", "02012345678", "01312345678", "phone", ""])
def test_invalid_sender_number_is_rejected(reader, sender: str) -> None:
    assert submit_topup(reader, sender=sender).status_code in {400, 422}


@pytest.mark.parametrize("amount", ["0", "-5", "50001", "12.5", "abc"])
def test_invalid_amount_is_rejected(reader, amount: str) -> None:
    assert submit_topup(reader, amount=amount).status_code == 422


def test_unsupported_method_is_rejected(reader) -> None:
    response = client.post(
        "/api/v1/wallet/topups",
        data={"method": "card", "sender_number": "01012345678", "amount_egp": "100"},
        files={"receipt": ("r.png", PNG, "image/png")},
        headers=reader,
    )
    assert response.status_code == 400


def test_receipt_must_be_a_real_image(reader) -> None:
    assert submit_topup(reader, receipt=b"%PDF-1.7", content_type="application/pdf").status_code == 415
    # Declared as PNG but the bytes are not a PNG.
    assert submit_topup(reader, receipt=JPEG, content_type="image/png").status_code == 415


def test_oversized_receipt_is_rejected(reader) -> None:
    huge = PNG + b"\x00" * MAX_RECEIPT_SIZE_BYTES
    assert submit_topup(reader, receipt=huge).status_code == 413


def test_pending_topups_are_capped_per_user(reader) -> None:
    for _ in range(5):
        assert submit_topup(reader).status_code == 201
    assert submit_topup(reader).status_code == 409


# --- Moderator review ---

def test_moderator_sees_pending_topups_with_receipt(reader, moderator) -> None:
    submit_topup(reader)

    items = client.get("/api/v1/admin/wallet-topups", headers=moderator).json()["items"]

    assert len(items) == 1
    assert items[0]["user_email"] == "reader@example.com"
    assert items[0]["receipt_url"].startswith("data:image/png;base64,")
    assert "receipt_path" not in items[0]


def test_approval_credits_wallet_once(reader, moderator) -> None:
    topup_id = submit_topup(reader, amount="150").json()["id"]

    approved = client.post(f"/api/v1/admin/wallet-topups/{topup_id}/approve", headers=moderator)
    again = client.post(f"/api/v1/admin/wallet-topups/{topup_id}/approve", headers=moderator)

    assert approved.status_code == 200
    assert approved.json()["status"] == "approved"
    assert again.status_code == 409
    assert client.get("/api/v1/wallet", headers=reader).json()["balance_egp"] == 150
    assert client.get("/api/v1/admin/wallet-topups", headers=moderator).json()["items"] == []


def test_rejection_does_not_credit_and_shows_note(reader, moderator) -> None:
    topup_id = submit_topup(reader).json()["id"]

    response = client.post(
        f"/api/v1/admin/wallet-topups/{topup_id}/reject",
        json={"note": "No matching transfer found"},
        headers=moderator,
    )

    assert response.status_code == 200
    wallet = client.get("/api/v1/wallet", headers=reader).json()
    assert wallet["balance_egp"] == 0
    assert wallet["topups"][0]["status"] == "rejected"
    assert wallet["topups"][0]["review_note"] == "No matching transfer found"
    assert client.post(f"/api/v1/admin/wallet-topups/{topup_id}/approve", headers=moderator).status_code == 409


def test_review_of_unknown_or_malformed_topup(moderator) -> None:
    missing = "00000000-0000-0000-0000-000000000000"
    assert client.post(f"/api/v1/admin/wallet-topups/{missing}/approve", headers=moderator).status_code == 404
    assert client.post("/api/v1/admin/wallet-topups/not-a-uuid/approve", headers=moderator).status_code == 422


def test_overview_counts_pending_topups_and_balances(reader, moderator) -> None:
    submit_topup(reader, amount="100")
    approved_id = submit_topup(reader, amount="40").json()["id"]
    client.post(f"/api/v1/admin/wallet-topups/{approved_id}/approve", headers=moderator)

    overview = client.get("/api/v1/admin/overview", headers=moderator).json()

    assert overview["topups_pending"] == 1
    assert overview["topups_pending_egp"] == 100
    assert overview["wallet_balance_total_egp"] == 40
    assert overview["users_total"] == 2
    assert overview["listings_active"] >= 1


# --- Supabase mode: receipts go to the private bucket ---

def test_supabase_receipt_is_uploaded_to_private_bucket(monkeypatch: pytest.MonkeyPatch) -> None:
    user_id = "b797b8b4-09c5-4f7f-90d4-e89a595c6c4b"
    monkeypatch.setenv("RFOOF_STORAGE", "supabase")
    monkeypatch.setenv("SUPABASE_URL", "https://project.example")
    monkeypatch.setenv("SUPABASE_SECRET_KEY", "sb_secret_test-key")
    monkeypatch.setenv("AUTH_JWT_SECRET", "test-secret-" + "x" * 40)
    get_supabase_service.cache_clear()
    get_auth_settings.cache_clear()
    user = {"id": user_id, "email": "reader@example.com", "role": "user", "password_changed_at": None}
    monkeypatch.setattr(SupabaseService, "get_user", lambda self, requested: user if requested == user_id else None)
    token, _ = create_access_token(user_id)

    calls: list[tuple[str, str, object]] = []

    def fake_request(method: str, url: str, **kwargs: object) -> httpx.Response:
        calls.append((method, url, kwargs.get("json")))
        if "/rest/v1/wallet_topups" in url and method == "GET":
            return httpx.Response(200, json=[])
        if "/storage/v1/object/wallet-receipts/" in url:
            return httpx.Response(200, json={"Key": "ok"})
        if "/rest/v1/wallet_topups" in url and method == "POST":
            return httpx.Response(201, json=[{"id": "t1", **kwargs["json"], "status": "pending"}])
        return httpx.Response(500)

    monkeypatch.setattr(httpx, "request", fake_request)

    response = submit_topup({"Authorization": f"Bearer {token}"})

    assert response.status_code == 201
    upload_url = next(url for _, url, _ in calls if "/storage/" in url)
    assert upload_url.startswith(f"https://project.example/storage/v1/object/wallet-receipts/{user_id}/")
    inserted = next(body for method, url, body in calls if method == "POST" and "/rest/v1/wallet_topups" in url)
    assert inserted["user_id"] == user_id
    assert inserted["receipt_path"].startswith(f"{user_id}/")
    assert "public" not in inserted["receipt_path"]
