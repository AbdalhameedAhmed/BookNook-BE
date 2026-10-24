import base64
import json
from collections.abc import Iterator

import httpx
import pytest
from fastapi.testclient import TestClient

from app import main
from app.supabase_service import get_supabase_service


@pytest.fixture(autouse=True)
def clear_supabase_service_cache() -> Iterator[None]:
    get_supabase_service.cache_clear()
    yield
    get_supabase_service.cache_clear()


def test_supabase_auth_identity_is_used_to_create_listing(monkeypatch: pytest.MonkeyPatch) -> None:
    user_id = "b797b8b4-09c5-4f7f-90d4-e89a595c6c4b"
    monkeypatch.setenv("BOOKNOOK_STORAGE", "supabase")
    monkeypatch.setenv("SUPABASE_URL", "https://project.example")
    monkeypatch.setenv("SUPABASE_SECRET_KEY", "sb_secret_test-key")

    def fake_get(url: str, *, headers: dict[str, str], timeout: float) -> httpx.Response:
        assert url == "https://project.example/auth/v1/user"
        assert headers["apikey"] == "sb_secret_test-key"
        assert headers["Authorization"] == "Bearer valid-user-token"
        return httpx.Response(200, json={"id": user_id})

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

    monkeypatch.setattr(httpx, "get", fake_get)
    monkeypatch.setattr(httpx, "request", fake_request)
    client = TestClient(main.app)

    response = client.post(
        "/api/v1/listings",
        headers={"Authorization": "Bearer valid-user-token"},
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


def test_supabase_mode_does_not_accept_demo_tokens(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BOOKNOOK_STORAGE", "supabase")
    monkeypatch.setenv("SUPABASE_URL", "https://project.example")
    monkeypatch.setenv("SUPABASE_SECRET_KEY", "sb_secret_test-key")
    monkeypatch.setattr(
        httpx,
        "get",
        lambda *args, **kwargs: httpx.Response(401, json={"message": "bad token"}),
    )

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
    monkeypatch.setenv("BOOKNOOK_STORAGE", "supabase")
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
    monkeypatch.setenv("BOOKNOOK_STORAGE", "supabase")
    monkeypatch.setenv("SUPABASE_URL", "https://project.example")
    monkeypatch.setenv("SUPABASE_SECRET_KEY", "sb_secret_test-key")
    monkeypatch.setattr(
        httpx,
        "get",
        lambda *args, **kwargs: httpx.Response(200, json={"id": "b797b8b4-09c5-4f7f-90d4-e89a595c6c4b"}),
    )
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
        headers={"Authorization": "Bearer valid-user-token"},
        json={"message": "Interested"},
    )

    assert response.status_code == 201
    assert captured["method"] == "POST"
    assert captured["url"].endswith("/rpc/create_transaction_request")
    assert captured["json"]["p_buyer_id"] == "b797b8b4-09c5-4f7f-90d4-e89a595c6c4b"
    assert response.json()["status"] == "pending"


def test_supabase_request_history_is_loaded_for_participant(monkeypatch: pytest.MonkeyPatch) -> None:
    user_id = "b797b8b4-09c5-4f7f-90d4-e89a595c6c4b"
    monkeypatch.setenv("BOOKNOOK_STORAGE", "supabase")
    monkeypatch.setenv("SUPABASE_URL", "https://project.example")
    monkeypatch.setenv("SUPABASE_SECRET_KEY", "sb_secret_test-key")
    monkeypatch.setattr(httpx, "get", lambda *args, **kwargs: httpx.Response(200, json={"id": user_id}))
    captured_params: list[dict[str, str]] = []

    def fake_request(method: str, url: str, **kwargs: object) -> httpx.Response:
        captured_params.append(kwargs["params"])
        return httpx.Response(200, json=[])

    monkeypatch.setattr(httpx, "request", fake_request)
    response = TestClient(main.app).get(
        "/api/v1/me/requests",
        headers={"Authorization": "Bearer valid-user-token"},
    )

    assert response.status_code == 200
    assert response.json() == {"items": []}
    assert len(captured_params) == 2
    assert captured_params[0]["buyer_id"] == f"eq.{user_id}"
    assert captured_params[1]["listings.seller_id"] == f"eq.{user_id}"


def test_supabase_transaction_completion_uses_atomic_rpc(monkeypatch: pytest.MonkeyPatch) -> None:
    user_id = "b797b8b4-09c5-4f7f-90d4-e89a595c6c4b"
    monkeypatch.setenv("BOOKNOOK_STORAGE", "supabase")
    monkeypatch.setenv("SUPABASE_URL", "https://project.example")
    monkeypatch.setenv("SUPABASE_SECRET_KEY", "sb_secret_test-key")
    monkeypatch.setattr(httpx, "get", lambda *args, **kwargs: httpx.Response(200, json={"id": user_id}))
    captured: dict[str, object] = {}

    def fake_request(method: str, url: str, **kwargs: object) -> httpx.Response:
        captured.update({"method": method, "url": url, **kwargs})
        return httpx.Response(200, json={"id": "4ba9de59-2cdf-4856-b098-5e3ba5084fe3", "status": "completed"})

    monkeypatch.setattr(httpx, "request", fake_request)
    response = TestClient(main.app).post(
        "/api/v1/transactions/4ba9de59-2cdf-4856-b098-5e3ba5084fe3/complete",
        headers={"Authorization": "Bearer valid-user-token"},
    )

    assert response.status_code == 200
    assert captured["url"].endswith("/rpc/complete_book_transaction")
    assert captured["json"]["p_actor_id"] == user_id
    assert response.json()["status"] == "completed"


def test_supabase_report_is_persisted_for_authenticated_reporter(monkeypatch: pytest.MonkeyPatch) -> None:
    user_id = "b797b8b4-09c5-4f7f-90d4-e89a595c6c4b"
    monkeypatch.setenv("BOOKNOOK_STORAGE", "supabase")
    monkeypatch.setenv("SUPABASE_URL", "https://project.example")
    monkeypatch.setenv("SUPABASE_SECRET_KEY", "sb_secret_test-key")
    monkeypatch.setattr(httpx, "get", lambda *args, **kwargs: httpx.Response(200, json={"id": user_id}))
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
        headers={"Authorization": "Bearer valid-user-token"},
        json={"reported_user_id": user_id, "reason": "suspicious account"},
    )

    assert response.status_code == 201
    assert captured["url"].endswith("/rest/v1/reports")
    assert captured["json"]["reporter_id"] == user_id
    assert response.json()["status"] == "open"


def test_legacy_service_role_key_is_supported(monkeypatch: pytest.MonkeyPatch) -> None:
    claims = base64.urlsafe_b64encode(json.dumps({"role": "service_role"}).encode()).decode().rstrip("=")
    monkeypatch.setenv("BOOKNOOK_STORAGE", "supabase")
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
    monkeypatch.setenv("BOOKNOOK_STORAGE", "supabase")
    monkeypatch.setenv("SUPABASE_URL", "https://project.example")
    monkeypatch.setenv("SUPABASE_SECRET_KEY", "sb_publishable_example")

    with pytest.raises(RuntimeError, match="publishable/anon"):
        get_supabase_service()
