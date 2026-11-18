"""Promo codes: fixed EGP discount on the platform fee (memory mode).

The same rules are implemented in SQL; see supabase/tests/promo_codes_test.sql.
"""

import pytest
from fastapi.testclient import TestClient

from app import main
from tests.test_deals import FEE, User, revenue

client = TestClient(main.app)


@pytest.fixture
def mod() -> User:
    return User("mod@x.test", role="moderator")


def create_code(mod: User, code: str = "WELCOME5", discount: int = 5, max_uses: int = 2):
    return client.post("/api/v1/admin/promo-codes", headers=mod.headers,
                       json={"code": code, "discount_egp": discount, "max_uses": max_uses})


def uses(mod: User, code: str) -> int:
    items = client.get("/api/v1/admin/promo-codes", headers=mod.headers).json()["items"]
    return next(p["uses_count"] for p in items if p["code"] == code)


def request_with_code(user: User, listing_id: str, code: str, offers: list[str] | None = None):
    return client.post(f"/api/v1/listings/{listing_id}/requests", headers=user.headers,
                       json={"message": "", "offered_listing_ids": offers or [], "promo_code": code})


# --- Admin ---

def test_only_moderators_manage_promo_codes(mod) -> None:
    user = User("u@x.test")
    assert create_code(user).status_code == 403
    assert client.get("/api/v1/admin/promo-codes", headers=user.headers).status_code == 403
    assert client.post("/api/v1/admin/promo-codes", json={"code": "X", "discount_egp": 1, "max_uses": 1}).status_code == 401

    created = create_code(mod, " welcome5 ")
    assert created.status_code == 201
    assert created.json()["code"] == "WELCOME5"
    assert client.patch(f"/api/v1/admin/promo-codes/{created.json()['id']}", headers=user.headers, json={"active": False}).status_code == 403


@pytest.mark.parametrize("body", [
    {"code": "ab", "discount_egp": 5, "max_uses": 1},
    {"code": "has space", "discount_egp": 5, "max_uses": 1},
    {"code": "GOOD", "discount_egp": 0, "max_uses": 1},
    {"code": "GOOD", "discount_egp": 5, "max_uses": 0},
])
def test_invalid_promo_codes_are_rejected(mod, body) -> None:
    assert client.post("/api/v1/admin/promo-codes", headers=mod.headers, json=body).status_code == 422


def test_duplicate_code_names_are_rejected(mod) -> None:
    create_code(mod, "SAME")
    assert create_code(mod, "same").status_code == 409


# --- Using codes ---

def test_check_previews_without_using_the_code(mod) -> None:
    create_code(mod)
    user = User("u@x.test")

    assert client.get("/api/v1/promo-codes/welcome5").status_code == 401
    assert client.get("/api/v1/promo-codes/welcome5", headers=user.headers).json() == {"code": "WELCOME5", "discount_egp": 5}
    assert client.get("/api/v1/promo-codes/NOPE", headers=user.headers).status_code == 400
    assert uses(mod, "WELCOME5") == 0


def test_buyer_and_seller_codes_lower_their_own_fees(mod) -> None:
    create_code(mod, "WELCOME5", 5)
    create_code(mod, "BIG", 50)
    seller, buyer = User("seller@x.test"), User("buyer@x.test", balance=105)
    listing = seller.list_book(price=100)

    request = request_with_code(buyer, listing, "welcome5")
    assert request.status_code == 201, "105 is enough because the fee drops to 5"
    assert request.json()["held_egp"] == 105
    accept = client.patch(f"/api/v1/requests/{request.json()['id']}", headers=seller.headers,
                          json={"status": "accepted", "promo_code": "BIG"})
    assert accept.status_code == 200

    transaction = buyer.transaction_for(listing)
    assert (transaction["buyer_fee_egp"], transaction["seller_fee_egp"]) == (5, 0), "discount is capped at the fee"
    assert (transaction["buyer_discount_egp"], transaction["seller_discount_egp"]) == (5, FEE)
    buyer.confirm(transaction["id"])
    assert seller.balance == 100
    assert revenue() == 5


def test_code_limit_counts_book_deals_and_refunds_give_uses_back(mod) -> None:
    create_code(mod, "TWO", 5, max_uses=2)
    seller = User("seller@x.test")
    buyers = [User(f"b{i}@x.test", balance=500) for i in range(3)]
    books = [seller.list_book(price=20) for _ in range(3)]

    first = request_with_code(buyers[0], books[0], "TWO").json()["id"]
    assert request_with_code(buyers[1], books[1], "TWO").status_code == 201
    blocked = request_with_code(buyers[2], books[2], "TWO")
    assert blocked.status_code == 409
    assert buyers[2].balance == 500

    client.patch(f"/api/v1/requests/{first}", headers=seller.headers, json={"status": "rejected"})
    assert uses(mod, "TWO") == 1
    assert request_with_code(buyers[2], books[2], "TWO").status_code == 201


def test_switched_off_code_stops_working(mod) -> None:
    promo_id = create_code(mod, "OFF").json()["id"]
    seller, buyer = User("seller@x.test"), User("buyer@x.test", balance=500)
    listing = seller.list_book(price=20)

    assert client.patch(f"/api/v1/admin/promo-codes/{promo_id}", headers=mod.headers, json={"active": False}).status_code == 200

    assert request_with_code(buyer, listing, "OFF").status_code == 400
    assert buyer.balance == 500


def test_codes_do_not_apply_to_free_books(mod) -> None:
    create_code(mod, "FREEBIE")
    owner, reader = User("owner@x.test"), User("reader@x.test")
    assert request_with_code(reader, owner.list_book("free"), "FREEBIE").status_code == 400


def test_exchange_codes_and_moderator_cancel_release_both_uses(mod) -> None:
    create_code(mod, "SWAP", 10, max_uses=5)
    owner, requester = User("owner@x.test"), User("req@x.test")
    wanted, offer = owner.list_book("exchange"), requester.list_book("exchange")

    request = request_with_code(requester, wanted, "SWAP", [offer])
    assert request.status_code == 201, "fee fully discounted, empty wallet is fine"
    accept = client.patch(f"/api/v1/requests/{request.json()['id']}", headers=owner.headers,
                          json={"status": "accepted", "chosen_listing_id": offer, "promo_code": "SWAP"})
    assert accept.status_code == 200
    assert uses(mod, "SWAP") == 2

    tid = owner.transaction_for(wanted)["id"]
    client.post(f"/api/v1/admin/transactions/{tid}/cancel", headers=mod.headers)
    assert uses(mod, "SWAP") == 0
