"""Invite links and free requests (memory mode).

The same rules are implemented in SQL; see supabase/tests/referrals_test.sql.
"""

from fastapi.testclient import TestClient

from app import main
from core.security import create_access_token
from crud.crud_notifications import MEMORY_NOTIFICATION_STORE
from crud.crud_user import MEMORY_AUTH_STORE
from crud.crud_wallet import MEMORY_WALLET_STORE
from tests.test_deals import FEE, User, revenue

client = TestClient(main.app)


def invite_code(user: User) -> str:
    response = client.get("/api/v1/me/invite", headers=user.headers)
    assert response.status_code == 200, response.text
    return response.json()["invite_code"]


def summary(user: User) -> dict:
    return client.get("/api/v1/me/invite", headers=user.headers).json()


def sign_up_friend(email: str, code: str | None, balance: int = 0) -> User:
    response = client.post("/api/v1/auth/signup", json={
        "email": email, "password": "long-enough-password", "display_name": "Friend", "invite_code": code,
    })
    assert response.status_code == 202, response.text
    friend = User.__new__(User)
    friend.id = MEMORY_AUTH_STORE.find_user_by_email(email)["id"]
    friend.headers = {"Authorization": f"Bearer {create_access_token(friend.id)[0]}"}
    if balance:
        MEMORY_WALLET_STORE.adjust(friend.id, balance, "topup")
    return friend


def complete_sale(buyer: User, seller: User, price: int = 100) -> dict:
    listing = seller.list_book(price=price)
    request = buyer.request(listing)
    assert request.status_code == 201, request.text
    assert seller.decide(request.json()["id"], "accepted").status_code == 200
    transaction = buyer.transaction_for(listing)
    assert buyer.confirm(transaction["id"]).status_code == 200
    return transaction


def earn_free_request(inviter: User, email: str = "friend@example.com") -> User:
    friend = sign_up_friend(email, invite_code(inviter), balance=200)
    complete_sale(friend, User(f"shop-{email}"))
    assert summary(inviter)["free_requests_available"] == 1
    return friend


# --- Invite code ---

def test_invite_requires_sign_in_and_keeps_the_same_code() -> None:
    assert client.get("/api/v1/me/invite").status_code == 401
    user = User("inviter@x.test")
    first = summary(user)
    assert len(first["invite_code"]) == 8
    assert first == {"invite_code": first["invite_code"], "friends_joined": 0,
                     "free_requests_earned": 0, "free_requests_available": 0}
    assert invite_code(user) == first["invite_code"]
    assert invite_code(User("other@x.test")) != first["invite_code"]


def test_sign_up_from_link_records_the_friend_and_bad_codes_are_ignored() -> None:
    inviter = User("inviter@x.test")
    code = invite_code(inviter)

    sign_up_friend("a@example.com", code.lower())
    sign_up_friend("b@example.com", "NOTACODE")
    sign_up_friend("c@example.com", "bad code!")

    assert summary(inviter)["friends_joined"] == 1


def test_existing_accounts_are_not_attributed() -> None:
    inviter = User("inviter@x.test")
    User("taken@example.com")
    client.post("/api/v1/auth/signup", json={
        "email": "taken@example.com", "password": "long-enough-password", "display_name": "X",
        "invite_code": invite_code(inviter),
    })
    assert summary(inviter)["friends_joined"] == 0


# --- Earning ---

def test_friend_joining_alone_or_with_a_pending_request_earns_nothing() -> None:
    inviter = User("inviter@x.test")
    friend = sign_up_friend("friend@example.com", invite_code(inviter), balance=200)
    seller = User("seller@x.test")
    request = friend.request(seller.list_book(price=50))
    assert request.status_code == 201
    assert seller.decide(request.json()["id"], "accepted").status_code == 200

    assert summary(inviter)["free_requests_earned"] == 0, "accepted is not completed"


def test_completed_handover_earns_one_free_request_once_per_friend() -> None:
    inviter = User("inviter@x.test")
    friend = earn_free_request(inviter)
    MEMORY_WALLET_STORE.adjust(friend.id, 200, "topup")

    complete_sale(friend, User("seller2@x.test"))

    assert summary(inviter)["free_requests_earned"] == 1
    kinds = [n["kind"] for n in MEMORY_NOTIFICATION_STORE.notifications
             if n["user_id"] == inviter.id and n["kind"] != "listing_new"]
    assert kinds == ["referral_reward"]


def test_each_friend_earns_their_own_free_request() -> None:
    inviter = User("inviter@x.test")
    earn_free_request(inviter, "one@example.com")
    friend_two = sign_up_friend("two@example.com", invite_code(inviter), balance=200)
    complete_sale(friend_two, User("seller@x.test"))

    assert summary(inviter)["free_requests_available"] == 2


def test_deal_with_the_inviter_or_free_book_does_not_count() -> None:
    inviter = User("inviter@x.test", balance=100)
    friend = sign_up_friend("friend@example.com", invite_code(inviter), balance=200)

    complete_sale(friend, inviter)

    giver = User("giver@x.test")
    free_listing = giver.list_book("free")
    request = friend.request(free_listing)
    giver.decide(request.json()["id"], "accepted")
    friend.confirm(friend.transaction_for(free_listing)["id"])

    assert summary(inviter)["free_requests_earned"] == 0


def test_friend_selling_does_not_count() -> None:
    inviter = User("inviter@x.test")
    friend = sign_up_friend("friend@example.com", invite_code(inviter))
    complete_sale(User("buyer@x.test", balance=200), friend)

    assert summary(inviter)["free_requests_earned"] == 0


# --- Using ---

def test_free_request_waives_the_buyer_fee_on_the_next_request() -> None:
    inviter = User("inviter@x.test", balance=100)
    earn_free_request(inviter)
    seller = User("seller@x.test")
    listing = seller.list_book(price=100)

    request = inviter.request(listing)

    assert request.status_code == 201, "100 is enough because the fee is waived"
    body = request.json()
    assert (body["fee_egp"], body["fee_discount_egp"], body["held_egp"], body["free_request_used"]) == (0, FEE, 100, True)
    assert summary(inviter)["free_requests_available"] == 0

    before = revenue()
    seller.decide(body["id"], "accepted")
    inviter.confirm(inviter.transaction_for(listing)["id"])
    assert revenue() - before == FEE, "only the seller's fee is revenue"


def test_free_request_waives_the_seller_fee_when_accepting_first() -> None:
    inviter = User("inviter@x.test")
    earn_free_request(inviter)
    buyer = User("buyer@x.test", balance=200)
    listing = inviter.list_book(price=100)
    request = buyer.request(listing)

    accept = inviter.decide(request.json()["id"], "accepted")

    assert accept.json()["free_request_used"] is True
    transaction = buyer.transaction_for(listing)
    assert (transaction["seller_fee_egp"], transaction["seller_discount_egp"]) == (0, FEE)
    buyer.confirm(transaction["id"])
    assert inviter.balance == 100, "full price, no fee"


def test_refunded_request_gives_the_free_request_back() -> None:
    inviter = User("inviter@x.test", balance=200)
    earn_free_request(inviter)
    listing = User("seller@x.test").list_book(price=100)

    request = inviter.request(listing).json()
    assert summary(inviter)["free_requests_available"] == 0
    assert inviter.decide(request["id"], "cancelled").status_code == 200

    assert summary(inviter)["free_requests_available"] == 1
    assert inviter.balance == 200


def test_cancelled_handover_gives_the_free_request_back() -> None:
    inviter = User("inviter@x.test", balance=200)
    earn_free_request(inviter)
    seller = User("seller@x.test")
    listing = seller.list_book(price=100)
    seller.decide(inviter.request(listing).json()["id"], "accepted")
    mod = User("mod@x.test", role="moderator")

    response = client.post(f"/api/v1/admin/transactions/{inviter.transaction_for(listing)['id']}/cancel", headers=mod.headers)

    assert response.status_code == 200, response.text
    assert summary(inviter)["free_requests_available"] == 1


def test_promo_code_keeps_the_free_request_for_later() -> None:
    mod = User("mod@x.test", role="moderator")
    client.post("/api/v1/admin/promo-codes", headers=mod.headers, json={"code": "FIVE", "discount_egp": 5, "max_uses": 5})
    inviter = User("inviter@x.test", balance=200)
    earn_free_request(inviter)
    listing = User("seller@x.test").list_book(price=100)

    response = client.post(f"/api/v1/listings/{listing}/requests", headers=inviter.headers,
                           json={"message": "", "offered_listing_ids": [], "promo_code": "FIVE"})

    assert response.json()["free_request_used"] is False
    assert response.json()["fee_egp"] == FEE - 5
    assert summary(inviter)["free_requests_available"] == 1


def test_free_listings_do_not_use_the_free_request() -> None:
    inviter = User("inviter@x.test")
    earn_free_request(inviter)
    response = inviter.request(User("giver@x.test").list_book("free"))

    assert response.json()["free_request_used"] is False
    assert summary(inviter)["free_requests_available"] == 1


def test_users_without_free_requests_pay_the_fee() -> None:
    buyer = User("buyer@x.test", balance=200)
    response = buyer.request(User("seller@x.test").list_book(price=100))
    assert (response.json()["fee_egp"], response.json()["free_request_used"]) == (FEE, False)


def test_wallet_reports_free_requests_for_the_fee_preview() -> None:
    inviter = User("inviter@x.test")
    assert client.get("/api/v1/wallet", headers=inviter.headers).json()["free_requests_available"] == 0
    earn_free_request(inviter)
    assert client.get("/api/v1/wallet", headers=inviter.headers).json()["free_requests_available"] == 1
