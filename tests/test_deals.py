"""Wallet-paid sales and exchanges, handover confirmation, and chat (memory mode).

The same rules are implemented in SQL for Supabase; see supabase/tests/wallet_deals_test.sql.
"""

import pytest
from fastapi.testclient import TestClient

from app import main
from core.security import create_access_token
from crud.crud_user import MEMORY_AUTH_STORE
from crud.crud_wallet import MEMORY_WALLET_STORE

client = TestClient(main.app)
FEE = 10


class User:
    def __init__(self, email: str, role: str = "user", balance: int = 0) -> None:
        record = MEMORY_AUTH_STORE.register_user(email, "unused-hash", email.split("@")[0].title())
        MEMORY_AUTH_STORE.users[record["id"]]["role"] = role
        self.id = record["id"]
        self.headers = {"Authorization": f"Bearer {create_access_token(self.id)[0]}"}
        if balance:
            MEMORY_WALLET_STORE.adjust(self.id, balance, "topup")

    @property
    def balance(self) -> int:
        return MEMORY_WALLET_STORE.get_balance(self.id)

    def list_book(self, listing_type: str = "sale", price: int | None = 100) -> str:
        response = client.post("/api/v1/listings", headers=self.headers, json={
            "title": f"{listing_type} book", "condition": "good", "listing_type": listing_type,
            "price_egp": price if listing_type == "sale" else None, "area": "Fayoum Center",
        })
        assert response.status_code == 201, response.text
        return response.json()["id"]

    def request(self, listing_id: str, offers: list[str] | None = None):
        return client.post(f"/api/v1/listings/{listing_id}/requests", headers=self.headers,
                           json={"message": "Hi", "offered_listing_ids": offers or []})

    def decide(self, request_id: str, status: str, chosen: str | None = None):
        return client.patch(f"/api/v1/requests/{request_id}", headers=self.headers,
                            json={"status": status, "chosen_listing_id": chosen})

    def confirm(self, transaction_id: str):
        return client.post(f"/api/v1/transactions/{transaction_id}/complete", headers=self.headers)

    def transaction_for(self, listing_id: str) -> dict:
        items = client.get("/api/v1/me/transactions", headers=self.headers).json()["items"]
        return next(t for t in items if t["listing_id"] == listing_id)


def listing_status(listing_id: str) -> str:
    return client.get(f"/api/v1/listings/{listing_id}").json()["status"]


def revenue() -> int:
    return sum(e["amount_egp"] for e in MEMORY_WALLET_STORE.entries if e["kind"] == "fee")


# --- Sale ---

def test_sale_request_needs_price_plus_fee_in_wallet() -> None:
    seller, buyer = User("seller@x.test"), User("buyer@x.test", balance=109)
    listing = seller.list_book(price=100)

    response = buyer.request(listing)

    assert response.status_code == 402
    assert response.json()["detail"] == "Insufficient wallet balance"
    assert buyer.balance == 109
    assert client.get("/api/v1/me/requests", headers=seller.headers).json()["items"] == []


def test_sale_request_holds_price_plus_fee() -> None:
    seller, buyer = User("seller@x.test"), User("buyer@x.test", balance=150)
    listing = seller.list_book(price=100)

    response = buyer.request(listing)

    assert response.status_code == 201
    assert response.json()["held_egp"] == 110
    wallet = client.get("/api/v1/wallet", headers=buyer.headers).json()
    assert wallet["balance_egp"] == 40
    assert wallet["held_egp"] == 110
    assert wallet["platform_fee_egp"] == FEE
    assert wallet["entries"][0]["kind"] == "hold"


def test_full_sale_pays_seller_price_minus_fee_after_buyer_confirms() -> None:
    seller, buyer, rival = User("seller@x.test"), User("buyer@x.test", balance=200), User("rival@x.test", balance=200)
    listing = seller.list_book(price=100)
    request_id = buyer.request(listing).json()["id"]
    assert rival.request(listing).status_code == 201

    assert seller.decide(request_id, "accepted").status_code == 200
    assert rival.balance == 200, "competing request is refunded in full"
    assert listing_status(listing) == "reserved"

    transaction = buyer.transaction_for(listing)
    assert (transaction["price_egp"], transaction["buyer_fee_egp"], transaction["seller_fee_egp"]) == (100, FEE, FEE)
    assert seller.confirm(transaction["id"]).status_code == 403, "only the buyer confirms receipt"
    assert rival.confirm(transaction["id"]).status_code == 403

    response = buyer.confirm(transaction["id"])

    assert response.status_code == 200
    assert response.json()["status"] == "completed"
    assert seller.balance == 90
    assert buyer.balance == 90
    assert revenue() == 2 * FEE
    assert buyer.confirm(transaction["id"]).status_code == 409


def test_rejected_and_cancelled_sale_requests_are_refunded_in_full() -> None:
    seller, buyer = User("seller@x.test"), User("buyer@x.test", balance=110)
    listing = seller.list_book(price=100)

    first = buyer.request(listing).json()["id"]
    assert seller.decide(first, "cancelled").status_code == 403
    assert seller.decide(first, "rejected").status_code == 200
    assert buyer.balance == 110

    second = buyer.request(listing).json()["id"]
    assert buyer.decide(second, "accepted").status_code == 403
    assert buyer.decide(second, "cancelled").status_code == 200
    assert buyer.balance == 110
    assert revenue() == 0


def test_closing_listing_refunds_pending_but_not_during_handover() -> None:
    seller, buyer = User("seller@x.test"), User("buyer@x.test", balance=300)
    listing = seller.list_book(price=100)
    buyer.request(listing)

    assert client.post(f"/api/v1/listings/{listing}/deactivate", headers=seller.headers).status_code == 200
    assert buyer.balance == 300
    assert buyer.request(listing).status_code == 400

    other = seller.list_book(price=100)
    seller.decide(buyer.request(other).json()["id"], "accepted")
    assert client.post(f"/api/v1/listings/{other}/mark-complete", headers=seller.headers).status_code == 409
    assert client.post(f"/api/v1/listings/{other}/deactivate", headers=seller.headers).status_code == 409


def test_fee_is_configurable_and_seller_fee_never_exceeds_price(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PLATFORM_FEE_EGP", "25")
    seller, buyer = User("seller@x.test"), User("buyer@x.test", balance=100)
    listing = seller.list_book(price=15)

    assert client.get("/api/v1/fees").json() == {"platform_fee_egp": 25}
    request_id = buyer.request(listing).json()["id"]
    assert buyer.balance == 60
    seller.decide(request_id, "accepted")
    transaction = buyer.transaction_for(listing)
    assert transaction["seller_fee_egp"] == 15
    buyer.confirm(transaction["id"])
    assert seller.balance == 0
    assert revenue() == 40


def test_free_listings_cost_nothing() -> None:
    owner, reader = User("owner@x.test"), User("reader@x.test")
    listing = owner.list_book("free")

    request_id = reader.request(listing).json()["id"]
    owner.decide(request_id, "accepted")
    assert owner.confirm(reader.transaction_for(listing)["id"]).status_code == 200
    assert reader.balance == 0 and owner.balance == 0 and revenue() == 0


# --- Exchange ---

def test_exchange_request_validates_offers_and_fee() -> None:
    owner, requester, stranger = User("owner@x.test"), User("req@x.test"), User("str@x.test")
    wanted = owner.list_book("exchange")
    mine = requester.list_book("exchange")
    theirs = stranger.list_book("exchange")

    assert requester.request(wanted).status_code == 400, "at least one offer is required"
    assert requester.request(wanted, [theirs]).status_code == 400, "cannot offer someone else's book"
    assert requester.request(wanted, [mine]).status_code == 402, "fee must be in the wallet"

    client.post(f"/api/v1/listings/{mine}/deactivate", headers=requester.headers)
    MEMORY_WALLET_STORE.adjust(requester.id, FEE, "topup")
    assert requester.request(wanted, [mine]).status_code == 400, "cannot offer an inactive book"


def test_full_exchange_takes_both_fees_and_needs_both_confirmations() -> None:
    owner, requester = User("owner@x.test"), User("req@x.test", balance=FEE)
    wanted = owner.list_book("exchange")
    offer_a, offer_b = requester.list_book("exchange"), requester.list_book("sale", 40)

    request = requester.request(wanted, [offer_a, offer_b])
    assert request.status_code == 201
    assert requester.balance == 0
    request_id = request.json()["id"]
    incoming = client.get("/api/v1/me/requests", headers=owner.headers).json()["items"][0]
    assert {o["listing_id"] for o in incoming["offers"]} == {offer_a, offer_b}

    assert owner.decide(request_id, "accepted").status_code == 400, "owner must choose a book"
    assert owner.decide(request_id, "accepted", wanted).status_code == 400, "choice must be an offered book"
    assert owner.decide(request_id, "accepted", offer_a).status_code == 402, "owner pays the fee too"
    assert listing_status(wanted) == "active"

    MEMORY_WALLET_STORE.adjust(owner.id, FEE, "topup")
    assert owner.decide(request_id, "accepted", offer_a).status_code == 200
    assert owner.balance == 0
    assert listing_status(offer_b) == "active", "unchosen offer stays available"

    transaction = owner.transaction_for(wanted)
    assert transaction["offered_listing_id"] == offer_a
    assert requester.confirm(transaction["id"]).json()["status"] == "arranging"
    assert requester.confirm(transaction["id"]).status_code == 409
    assert revenue() == 0

    assert owner.confirm(transaction["id"]).json()["status"] == "completed"
    assert revenue() == 2 * FEE
    mine = {item["id"]: item["status"] for item in client.get("/api/v1/me/listings", headers=requester.headers).json()["items"]}
    assert mine[offer_a] == "exchanged"


# --- Chat ---

def test_only_participants_can_chat_while_handover_is_open() -> None:
    seller, buyer, outsider = User("seller@x.test"), User("buyer@x.test", balance=200), User("out@x.test")
    listing = seller.list_book(price=100)
    seller.decide(buyer.request(listing).json()["id"], "accepted")
    tid = buyer.transaction_for(listing)["id"]
    url = f"/api/v1/transactions/{tid}/messages"

    assert client.get(url).status_code == 401
    assert client.get(url, headers=outsider.headers).status_code == 403
    assert client.post(url, headers=outsider.headers, json={"body": "hi"}).status_code == 403
    assert client.post(url, headers=buyer.headers, json={"body": "   "}).status_code == 422
    assert client.post(url, headers=buyer.headers, json={"body": "x" * 1001}).status_code == 422

    assert client.post(url, headers=buyer.headers, json={"body": "Library at 5?"}).status_code == 201
    assert client.post(url, headers=seller.headers, json={"body": "See you there"}).status_code == 201
    thread = client.get(url, headers=seller.headers).json()["items"]
    assert [(m["sender_id"], m["body"]) for m in thread] == [(buyer.id, "Library at 5?"), (seller.id, "See you there")]

    buyer.confirm(tid)
    assert client.post(url, headers=buyer.headers, json={"body": "thanks"}).status_code == 409
    assert client.get(url, headers=buyer.headers).status_code == 200


def test_chat_on_unknown_transaction() -> None:
    user = User("u@x.test")
    assert client.get("/api/v1/transactions/missing/messages", headers=user.headers).status_code == 404


# --- Moderation ---

def test_moderator_can_cancel_handover_and_refund_everyone() -> None:
    owner, requester, mod = User("owner@x.test", balance=FEE), User("req@x.test", balance=FEE), User("m@x.test", "moderator")
    wanted, offer = owner.list_book("exchange"), requester.list_book("exchange")
    owner.decide(requester.request(wanted, [offer]).json()["id"], "accepted", offer)
    tid = owner.transaction_for(wanted)["id"]

    assert client.post(f"/api/v1/admin/transactions/{tid}/cancel", headers=owner.headers).status_code == 403
    listed = client.get("/api/v1/admin/transactions", headers=mod.headers).json()["items"]
    assert [t["id"] for t in listed] == [tid]

    response = client.post(f"/api/v1/admin/transactions/{tid}/cancel", headers=mod.headers)

    assert response.status_code == 200
    assert owner.balance == FEE and requester.balance == FEE
    assert listing_status(wanted) == "active" and listing_status(offer) == "active"
    assert revenue() == 0
    overview = client.get("/api/v1/admin/overview", headers=mod.headers).json()
    assert overview["held_total_egp"] == 0 and overview["platform_revenue_egp"] == 0


def test_owner_cannot_reopen_reserved_listing_by_editing_status() -> None:
    seller, buyer = User("seller@x.test"), User("buyer@x.test", balance=200)
    listing = seller.list_book(price=100)
    seller.decide(buyer.request(listing).json()["id"], "accepted")

    client.patch(f"/api/v1/listings/{listing}", headers=seller.headers, json={"status": "active"})

    assert listing_status(listing) == "reserved"
