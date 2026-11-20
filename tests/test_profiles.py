"""Public seller profiles: GET /users/{id} and the seller filter on GET /listings."""

import httpx
import pytest

from crud.crud_user import MEMORY_AUTH_STORE
from tests.test_deals import User, client


def test_profile_shows_public_facts_only() -> None:
    seller = User("seller@x.test")
    sale, swap = seller.list_book(price=100), seller.list_book("exchange", None)
    seller.list_book("free", None)
    MEMORY_AUTH_STORE.set_avatar_url(seller.id, "https://cdn.example/s.png")

    profile = client.get(f"/api/v1/users/{seller.id}").json()

    assert profile["display_name"] == "Seller" and profile["avatar_url"] == "https://cdn.example/s.png"
    assert profile["available"] == {"total": 3, "sale": 1, "exchange": 1, "free": 1}
    assert profile["handed_over"] == 0 and profile["area"] and profile["member_since"]
    assert set(profile) == {"id", "display_name", "avatar_url", "area", "member_since", "available", "handed_over"}, \
        "no email, phone, wallet or requests"
    assert sale and swap


def test_unknown_profile_is_404() -> None:
    assert client.get("/api/v1/users/no-such-user").status_code == 404
    assert client.get("/api/v1/users/" + "a" * 65).status_code == 404


def test_handed_over_counts_completed_sales_and_given_swap_books() -> None:
    seller, buyer = User("seller@x.test"), User("buyer@x.test", balance=500)
    listing = seller.list_book(price=100)
    request_id = buyer.request(listing).json()["id"]
    seller.decide(request_id, "accepted")
    buyer.confirm(buyer.transaction_for(listing)["id"])

    assert client.get(f"/api/v1/users/{seller.id}").json()["handed_over"] == 1
    assert client.get(f"/api/v1/users/{buyer.id}").json()["handed_over"] == 0, "buying is not handing over"
    assert client.get(f"/api/v1/users/{seller.id}").json()["available"]["total"] == 0


def test_listings_filter_by_seller_and_sort_by_price() -> None:
    seller, other = User("seller@x.test"), User("other@x.test")
    cheap, dear = seller.list_book(price=30), seller.list_book(price=90)
    free = seller.list_book("free", None)
    other.list_book(price=10)

    newest = client.get("/api/v1/listings", params={"seller_id": seller.id}).json()
    by_price = client.get("/api/v1/listings", params={"seller_id": seller.id, "sort": "price_asc"}).json()

    assert newest["total"] == 3 and {item["seller_id"] for item in newest["items"]} == {seller.id}
    assert [item["id"] for item in by_price["items"]] == [cheap, dear, free], "free (no price) last"
    assert client.get("/api/v1/listings", params={"sort": "random"}).status_code == 422


def test_users_can_report_others_but_not_themselves() -> None:
    reporter, target = User("reporter@x.test"), User("target@x.test")
    ok = client.post("/api/v1/reports", headers=reporter.headers, json={"reported_user_id": target.id, "reason": "Rude messages"})
    self_report = client.post("/api/v1/reports", headers=reporter.headers, json={"reported_user_id": reporter.id, "reason": "Rude"})
    unknown = client.post("/api/v1/reports", headers=reporter.headers, json={"reported_user_id": "ghost", "reason": "Rude"})
    assert ok.status_code == 201 and self_report.status_code == 400 and unknown.status_code == 404


def test_supabase_profile_uses_one_rpc_and_seller_filter(monkeypatch: pytest.MonkeyPatch) -> None:
    from core.auth_settings import get_auth_settings
    from supabase_service import get_supabase_service

    user_id = "b797b8b4-09c5-4f7f-90d4-e89a595c6c4b"
    monkeypatch.setenv("RFOOF_STORAGE", "supabase")
    monkeypatch.setenv("SUPABASE_URL", "https://project.example")
    monkeypatch.setenv("SUPABASE_SECRET_KEY", "sb_secret_test-key")
    monkeypatch.setenv("AUTH_JWT_SECRET", "test-secret-" + "x" * 40)
    get_supabase_service.cache_clear()
    get_auth_settings.cache_clear()
    calls: list[tuple[str, object]] = []
    profile = {"id": user_id, "display_name": "Mona", "available": {"total": 2}}

    def fake_request(method: str, url: str, *, json=None, params=None, **_: object) -> httpx.Response:
        name = url.rsplit("/", 1)[-1]
        calls.append((name, json if method == "POST" else params))
        if name == "public_profile":
            return httpx.Response(200, json=profile)
        return httpx.Response(200, json=[], headers={"content-range": "*/0"})

    monkeypatch.setattr(httpx, "request", fake_request)
    try:
        found = client.get(f"/api/v1/users/{user_id}")
        not_uuid = client.get("/api/v1/users/not-a-uuid")
        client.get("/api/v1/listings", params={"seller_id": user_id, "sort": "price_asc"})
        bad_seller = client.get("/api/v1/listings", params={"seller_id": "x,or=(id.eq.1)"}).json()
    finally:
        get_supabase_service.cache_clear()
        get_auth_settings.cache_clear()

    assert found.json() == profile and not_uuid.status_code == 404
    assert calls[0] == ("public_profile", {"p_user_id": user_id})
    listing_params = calls[1][1]
    assert listing_params["seller_id"] == f"eq.{user_id}" and listing_params["status"] == "eq.active"
    assert listing_params["order"] == "price_egp.asc.nullslast,created_at.desc"
    assert bad_seller["items"] == [] and len(calls) == 2, "a non-UUID seller id never reaches the database"
