import base64
import json
from collections.abc import Iterator

import httpx
import pytest
from fastapi.testclient import TestClient

from app import main
from core.auth_settings import get_auth_settings
from core.security import create_access_token
from supabase_service import SupabaseService, get_supabase_service


@pytest.fixture(autouse=True)
def clear_supabase_service_cache(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("AUTH_JWT_SECRET", "test-secret-" + "x" * 40)
    monkeypatch.setenv("EMAIL_BACKEND", "console")
    get_supabase_service.cache_clear()
    get_auth_settings.cache_clear()
    yield
    get_supabase_service.cache_clear()
    get_auth_settings.cache_clear()


def sign_in_as(monkeypatch: pytest.MonkeyPatch, user_id: str) -> dict[str, str]:
    """Issues a real backend access token and makes the users-table lookup return this user."""
    user = {"id": user_id, "email": "reader@example.com", "role": "user", "password_changed_at": None}
    monkeypatch.setattr(SupabaseService, "get_user", lambda self, requested_id: user if requested_id == user_id else None)
    token, _ = create_access_token(user_id)
    return {"Authorization": f"Bearer {token}"}


def test_supabase_auth_identity_is_used_to_create_listing(monkeypatch: pytest.MonkeyPatch) -> None:
    user_id = "b797b8b4-09c5-4f7f-90d4-e89a595c6c4b"
    monkeypatch.setenv("RFOOF_STORAGE", "supabase")
    monkeypatch.setenv("SUPABASE_URL", "https://project.example")
    monkeypatch.setenv("SUPABASE_SECRET_KEY", "sb_secret_test-key")


    captured: dict[str, object] = {}

    def fake_request(
        method: str,
        url: str,
        *,
        params: dict[str, str],
        json: dict[str, object],
        headers: dict[str, str],
        timeout: float,
    ) -> httpx.Response:
        captured.update({"method": method, "url": url, "payload": json, "headers": headers})
        return httpx.Response(
            201,
            json=[
                {
                    "id": "f1b0ab0f-fdbc-4d7b-8e1b-e169cb7e3972",
                    **json,
                    "status": "active",
                    "created_at": "2026-10-24T00:00:00+00:00",
                    "profiles": {"display_name": "Reader"},
                }
            ],
        )

    auth_headers = sign_in_as(monkeypatch, user_id)
    monkeypatch.setattr(httpx, "request", fake_request)
    client = TestClient(main.app)

    response = client.post(
        "/api/v1/listings",
        headers=auth_headers,
        json={
            "title": "A Real Supabase Book",
            "condition": "good",
            "listing_type": "sale",
            "price_egp": 125,
            "area": "Fayoum Center",
        },
    )

    assert response.status_code == 201
    assert captured["method"] == "POST"
    assert captured["headers"]["apikey"] == "sb_secret_test-key"
    assert "Authorization" not in captured["headers"]
    assert captured["payload"]["seller_id"] == user_id
    assert response.json()["seller_name"] == "Reader"


def test_authenticated_book_photo_upload_uses_supabase_storage(monkeypatch: pytest.MonkeyPatch) -> None:
    user_id = "b797b8b4-09c5-4f7f-90d4-e89a595c6c4b"
    monkeypatch.setenv("RFOOF_STORAGE", "supabase")
    monkeypatch.setenv("SUPABASE_URL", "https://project.example")
    monkeypatch.setenv("SUPABASE_SECRET_KEY", "sb_secret_test-key")
    auth_headers = sign_in_as(monkeypatch, user_id)
    captured: dict[str, object] = {}

    def fake_post(url: str, *, content: bytes, headers: dict[str, str], timeout: float) -> httpx.Response:
        captured.update({"url": url, "content": content, "headers": headers})
        return httpx.Response(200, json={"Key": "book-images/path.png"})

    monkeypatch.setattr(httpx, "post", fake_post)
    content = b"\x89PNG\r\n\x1a\nsmall image"
    response = TestClient(main.app).post(
        "/api/v1/listing-images",
        headers=auth_headers,
        files={"file": ("book.png", content, "image/png")},
    )

    assert response.status_code == 201
    assert captured["url"].startswith(f"https://project.example/storage/v1/object/book-images/{user_id}/")
    assert captured["content"] == content
    assert captured["headers"]["apikey"] == "sb_secret_test-key"
    assert response.json()["url"].startswith("https://project.example/storage/v1/object/public/book-images/")


def test_supabase_mode_does_not_accept_demo_tokens(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RFOOF_STORAGE", "supabase")
    monkeypatch.setenv("SUPABASE_URL", "https://project.example")
    monkeypatch.setenv("SUPABASE_SECRET_KEY", "sb_secret_test-key")

    response = TestClient(main.app).post(
        "/api/v1/listings",
        headers={"Authorization": "Bearer demo-user-1"},
        json={
            "title": "A Real Supabase Book",
            "condition": "good",
            "listing_type": "sale",
            "price_egp": 125,
            "area": "Fayoum Center",
        },
    )

    assert response.status_code == 401


def test_public_supabase_listing_detail_filters_to_active_status(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RFOOF_STORAGE", "supabase")
    monkeypatch.setenv("SUPABASE_URL", "https://project.example")
    monkeypatch.setenv("SUPABASE_SECRET_KEY", "sb_secret_test-key")
    captured: dict[str, object] = {}

    def fake_request(method: str, url: str, **kwargs: object) -> httpx.Response:
        captured.update(kwargs)
        return httpx.Response(200, json=[])

    monkeypatch.setattr(httpx, "request", fake_request)

    response = TestClient(main.app).get("/api/v1/listings/00000000-0000-0000-0000-000000000001")

    assert response.status_code == 404
    assert captured["params"]["status"] == "eq.active"


def test_supabase_mode_persists_request_through_transactional_rpc(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RFOOF_STORAGE", "supabase")
    monkeypatch.setenv("SUPABASE_URL", "https://project.example")
    monkeypatch.setenv("SUPABASE_SECRET_KEY", "sb_secret_test-key")
    auth_headers = sign_in_as(monkeypatch, "b797b8b4-09c5-4f7f-90d4-e89a595c6c4b")
    captured: dict[str, object] = {}

    def fake_request(method: str, url: str, **kwargs: object) -> httpx.Response:
        captured.update({"method": method, "url": url, **kwargs})
        return httpx.Response(
            200,
            json={
                "id": "16f1d334-5486-45b9-9c81-f084fc283df2",
                "listing_id": "8ba31cee-9c47-4e77-90b8-09ea9747b19f",
                "buyer_id": "b797b8b4-09c5-4f7f-90d4-e89a595c6c4b",
                "seller_id": "c797b8b4-09c5-4f7f-90d4-e89a595c6c4b",
                "message": "Interested",
                "status": "pending",
            },
        )

    monkeypatch.setattr(httpx, "request", fake_request)

    response = TestClient(main.app).post(
        "/api/v1/listings/8ba31cee-9c47-4e77-90b8-09ea9747b19f/requests",
        headers=auth_headers,
        json={"message": "Interested"},
    )

    assert response.status_code == 201
    assert captured["method"] == "POST"
    assert captured["url"].endswith("/rpc/create_transaction_request")
    assert captured["json"]["p_buyer_id"] == "b797b8b4-09c5-4f7f-90d4-e89a595c6c4b"
    assert response.json()["status"] == "pending"


def test_supabase_request_history_is_loaded_for_participant(monkeypatch: pytest.MonkeyPatch) -> None:
    user_id = "b797b8b4-09c5-4f7f-90d4-e89a595c6c4b"
    monkeypatch.setenv("RFOOF_STORAGE", "supabase")
    monkeypatch.setenv("SUPABASE_URL", "https://project.example")
    monkeypatch.setenv("SUPABASE_SECRET_KEY", "sb_secret_test-key")
    auth_headers = sign_in_as(monkeypatch, user_id)
    captured_params: list[dict[str, str]] = []

    def fake_request(method: str, url: str, **kwargs: object) -> httpx.Response:
        captured_params.append(kwargs["params"])
        return httpx.Response(200, json=[])

    monkeypatch.setattr(httpx, "request", fake_request)
    response = TestClient(main.app).get(
        "/api/v1/me/requests",
        headers=auth_headers,
    )

    assert response.status_code == 200
    assert response.json() == {"items": []}
    assert len(captured_params) == 2
    assert captured_params[0]["buyer_id"] == f"eq.{user_id}"
    assert captured_params[1]["listings.seller_id"] == f"eq.{user_id}"


def test_supabase_transaction_completion_uses_atomic_rpc(monkeypatch: pytest.MonkeyPatch) -> None:
    user_id = "b797b8b4-09c5-4f7f-90d4-e89a595c6c4b"
    monkeypatch.setenv("RFOOF_STORAGE", "supabase")
    monkeypatch.setenv("SUPABASE_URL", "https://project.example")
    monkeypatch.setenv("SUPABASE_SECRET_KEY", "sb_secret_test-key")
    auth_headers = sign_in_as(monkeypatch, user_id)
    captured: dict[str, object] = {}

    def fake_request(method: str, url: str, **kwargs: object) -> httpx.Response:
        captured.update({"method": method, "url": url, **kwargs})
        return httpx.Response(200, json={"id": "4ba9de59-2cdf-4856-b098-5e3ba5084fe3", "status": "completed"})

    monkeypatch.setattr(httpx, "request", fake_request)
    response = TestClient(main.app).post(
        "/api/v1/transactions/4ba9de59-2cdf-4856-b098-5e3ba5084fe3/complete",
        headers=auth_headers,
    )

    assert response.status_code == 200
    assert captured["url"].endswith("/rpc/complete_book_transaction")
    assert captured["json"]["p_actor_id"] == user_id
    assert response.json()["status"] == "completed"


def test_supabase_report_is_persisted_for_authenticated_reporter(monkeypatch: pytest.MonkeyPatch) -> None:
    user_id = "b797b8b4-09c5-4f7f-90d4-e89a595c6c4b"
    monkeypatch.setenv("RFOOF_STORAGE", "supabase")
    monkeypatch.setenv("SUPABASE_URL", "https://project.example")
    monkeypatch.setenv("SUPABASE_SECRET_KEY", "sb_secret_test-key")
    auth_headers = sign_in_as(monkeypatch, user_id)
    captured: dict[str, object] = {}

    def fake_request(method: str, url: str, **kwargs: object) -> httpx.Response:
        captured.update({"method": method, "url": url, **kwargs})
        return httpx.Response(
            201,
            json=[{
                "id": "0dc41b11-071e-4b8b-a1eb-dbfba90c2a30",
                "reporter_id": user_id,
                "listing_id": None,
                "reported_user_id": user_id,
                "reason": "suspicious account",
                "status": "open",
            }],
        )

    monkeypatch.setattr(httpx, "request", fake_request)
    response = TestClient(main.app).post(
        "/api/v1/reports",
        headers=auth_headers,
        json={"reported_user_id": user_id, "reason": "suspicious account"},
    )

    assert response.status_code == 201
    assert captured["url"].endswith("/rest/v1/reports")
    assert captured["json"]["reporter_id"] == user_id
    assert response.json()["status"] == "open"


def test_legacy_service_role_key_is_supported(monkeypatch: pytest.MonkeyPatch) -> None:
    claims = base64.urlsafe_b64encode(json.dumps({"role": "service_role"}).encode()).decode().rstrip("=")
    monkeypatch.setenv("RFOOF_STORAGE", "supabase")
    monkeypatch.setenv("SUPABASE_URL", "https://project.example")
    legacy_key = f"eyJhbGciOiJIUzI1NiJ9.{claims}.signature"
    monkeypatch.setenv("SUPABASE_SECRET_KEY", legacy_key)

    service = get_supabase_service()

    assert service is not None

    captured: dict[str, object] = {}

    def fake_request(method: str, url: str, **kwargs: object) -> httpx.Response:
        captured.update(kwargs)
        return httpx.Response(200, json=[])

    monkeypatch.setattr(httpx, "request", fake_request)
    service.my_listings("b797b8b4-09c5-4f7f-90d4-e89a595c6c4b")
    assert captured["headers"]["Authorization"] == f"Bearer {legacy_key}"


def test_publishable_key_is_rejected_for_backend_storage(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RFOOF_STORAGE", "supabase")
    monkeypatch.setenv("SUPABASE_URL", "https://project.example")
    monkeypatch.setenv("SUPABASE_SECRET_KEY", "sb_publishable_example")

    with pytest.raises(RuntimeError, match="publishable/anon"):
        get_supabase_service()


def test_supabase_signup_handles_void_rpc_no_content_response(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RFOOF_STORAGE", "supabase")
    monkeypatch.setenv("SUPABASE_URL", "https://project.example")
    monkeypatch.setenv("SUPABASE_SECRET_KEY", "sb_secret_test-key")
    from api.v1.endpoints import auth as auth_endpoints

    sent: list[str] = []
    monkeypatch.setattr(auth_endpoints, "send_confirmation_email", lambda to, token: sent.append(to))
    calls: list[str] = []

    def fake_request(method: str, url: str, **kwargs: object) -> httpx.Response:
        calls.append(url)
        if url.endswith("/rpc/register_user"):
            return httpx.Response(
                200,
                json={"id": "b797b8b4-09c5-4f7f-90d4-e89a595c6c4b", "email": "new@example.com", "role": "user",
                      "email_confirmed_at": None, "password_changed_at": "2026-11-14T10:00:00+00:00"},
            )
        if url.endswith("/rpc/issue_auth_token"):
            return httpx.Response(204)  # PostgREST: VOID function -> No Content, empty body.
        raise AssertionError(f"unexpected call {url}")

    monkeypatch.setattr(httpx, "request", fake_request)

    response = TestClient(main.app).post(
        "/api/v1/auth/signup",
        json={"email": "new@example.com", "password": "long enough pw", "display_name": "New"},
    )

    assert response.status_code == 202
    assert calls[-1].endswith("/rpc/issue_auth_token")
    assert sent == ["new@example.com"]
