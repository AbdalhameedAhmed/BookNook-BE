"""Wallet withdrawals: users ask for a payout to their e-wallet; a moderator pays by hand and marks it paid."""

import pytest
from fastapi.testclient import TestClient

from app import main
from crud.crud_notifications import MEMORY_NOTIFICATION_STORE
from crud.crud_wallet import MEMORY_WALLET_STORE
from test_wallet import make_user, submit_topup

client = TestClient(main.app)
MISSING = "00000000-0000-0000-0000-000000000000"


@pytest.fixture
def reader() -> dict[str, str]:
    return make_user("reader@example.com")


@pytest.fixture
def moderator() -> dict[str, str]:
    return make_user("mod@example.com", role="moderator")


def fund(headers: dict[str, str], moderator: dict[str, str], amount: int) -> None:
    topup_id = submit_topup(headers, amount=str(amount)).json()["id"]
    assert client.post(f"/api/v1/admin/wallet-topups/{topup_id}/approve", headers=moderator).status_code == 200


def withdraw(headers: dict[str, str], amount: int = 100, number: str = "01012345678"):
    return client.post("/api/v1/wallet/withdrawals", json={"recipient_number": number, "amount_egp": amount},
                       headers=headers)


def balance(headers: dict[str, str]) -> int:
    return client.get("/api/v1/wallet", headers=headers).json()["balance_egp"]


# --- Authentication and authorization boundaries ---

def test_withdrawal_requires_authentication() -> None:
    assert withdraw({}).status_code == 401


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("GET", "/api/v1/admin/wallet-withdrawals"),
        ("POST", f"/api/v1/admin/wallet-withdrawals/{MISSING}/paid"),
        ("POST", f"/api/v1/admin/wallet-withdrawals/{MISSING}/reject"),
    ],
)
def test_withdrawal_admin_endpoints_reject_anonymous_and_regular_users(reader, method: str, path: str) -> None:
    assert client.request(method, path).status_code == 401
    assert client.request(method, path, headers=reader).status_code == 403


def test_user_cannot_mark_their_own_withdrawal_paid(reader, moderator) -> None:
    fund(reader, moderator, 300)
    withdrawal_id = withdraw(reader).json()["id"]

    assert client.post(f"/api/v1/admin/wallet-withdrawals/{withdrawal_id}/paid", headers=reader).status_code == 403
    assert client.post(f"/api/v1/admin/wallet-withdrawals/{withdrawal_id}/reject", headers=reader).status_code == 403
    assert balance(reader) == 200


def test_user_sees_only_their_own_withdrawals(reader, moderator) -> None:
    other = make_user("other@example.com")
    fund(other, moderator, 100)
    withdraw(other, 50)

    assert client.get("/api/v1/wallet", headers=reader).json()["withdrawals"] == []


# --- Validation ---

@pytest.mark.parametrize("number", ["0101234567", "02012345678", "abc", "+2001612345678"])
def test_invalid_numbers_are_rejected(reader, moderator, number: str) -> None:
    fund(reader, moderator, 100)
    assert withdraw(reader, 50, number).status_code == 400
    assert balance(reader) == 100


@pytest.mark.parametrize("amount", [0, -5, 50_001])
def test_out_of_range_amounts_are_rejected(reader, moderator, amount: int) -> None:
    fund(reader, moderator, 100)
    assert withdraw(reader, amount).status_code == 422


def test_cannot_withdraw_more_than_the_balance(reader, moderator) -> None:
    fund(reader, moderator, 100)

    response = withdraw(reader, 101)

    assert response.status_code == 402
    assert balance(reader) == 100
    assert client.get("/api/v1/wallet", headers=reader).json()["withdrawals"] == []


def test_pending_withdrawals_are_limited(reader, moderator) -> None:
    fund(reader, moderator, 100)
    for _ in range(3):
        assert withdraw(reader, 10).status_code == 201

    assert withdraw(reader, 10).status_code == 409
    assert balance(reader) == 70


# --- Money flow ---

def test_request_takes_the_amount_from_the_balance_immediately(reader, moderator) -> None:
    fund(reader, moderator, 300)

    created = withdraw(reader, 120, "+20 101 234 5678")

    assert created.status_code == 201
    body = created.json()
    assert body["status"] == "pending"
    assert body["recipient_number"] == "01012345678"
    assert "reviewed_by" not in body
    wallet = client.get("/api/v1/wallet", headers=reader).json()
    assert wallet["balance_egp"] == 180
    assert wallet["entries"][0]["kind"] == "withdrawal"
    assert wallet["entries"][0]["amount_egp"] == -120
    # The held amount cannot be withdrawn twice.
    assert withdraw(reader, 181).status_code == 402


def test_moderator_marks_paid_with_a_reference(reader, moderator) -> None:
    fund(reader, moderator, 300)
    withdrawal_id = withdraw(reader, 120).json()["id"]

    queue = client.get("/api/v1/admin/wallet-withdrawals", headers=moderator).json()["items"]
    assert [item["id"] for item in queue] == [withdrawal_id]
    assert queue[0]["user_email"] == "reader@example.com"
    assert queue[0]["topup_numbers"] == ["01012345678"]

    paid = client.post(f"/api/v1/admin/wallet-withdrawals/{withdrawal_id}/paid",
                       json={"note": "VF-123456"}, headers=moderator)

    assert paid.status_code == 200
    assert paid.json()["status"] == "paid"
    assert balance(reader) == 180  # Unchanged: it left the balance at request time.
    mine = client.get("/api/v1/wallet", headers=reader).json()["withdrawals"][0]
    assert mine["status"] == "paid" and mine["review_note"] == "VF-123456"
    assert client.get("/api/v1/admin/wallet-withdrawals", headers=moderator).json()["items"] == []
    overview = client.get("/api/v1/admin/overview", headers=moderator).json()
    assert overview["withdrawals_paid_egp"] == 120
    assert overview["withdrawals_pending"] == 0


def test_rejecting_returns_the_money(reader, moderator) -> None:
    fund(reader, moderator, 300)
    withdrawal_id = withdraw(reader, 120).json()["id"]

    rejected = client.post(f"/api/v1/admin/wallet-withdrawals/{withdrawal_id}/reject",
                           json={"note": "Number did not match"}, headers=moderator)

    assert rejected.status_code == 200
    assert balance(reader) == 300
    entries = client.get("/api/v1/wallet", headers=reader).json()["entries"]
    assert (entries[0]["kind"], entries[0]["amount_egp"]) == ("refund", 120)


def test_a_withdrawal_is_reviewed_only_once(reader, moderator) -> None:
    fund(reader, moderator, 300)
    withdrawal_id = withdraw(reader, 120).json()["id"]
    client.post(f"/api/v1/admin/wallet-withdrawals/{withdrawal_id}/paid", headers=moderator)

    assert client.post(f"/api/v1/admin/wallet-withdrawals/{withdrawal_id}/reject", headers=moderator).status_code == 409
    assert client.post(f"/api/v1/admin/wallet-withdrawals/{withdrawal_id}/paid", headers=moderator).status_code == 409
    assert balance(reader) == 180
    assert client.post(f"/api/v1/admin/wallet-withdrawals/{MISSING}/paid", headers=moderator).status_code == 404


def test_ledger_still_balances(reader, moderator) -> None:
    fund(reader, moderator, 500)
    paid_id = withdraw(reader, 100).json()["id"]
    rejected_id = withdraw(reader, 50).json()["id"]
    withdraw(reader, 25)
    client.post(f"/api/v1/admin/wallet-withdrawals/{paid_id}/paid", headers=moderator)
    client.post(f"/api/v1/admin/wallet-withdrawals/{rejected_id}/reject", headers=moderator)

    pending = sum(w["amount_egp"] for w in MEMORY_WALLET_STORE.withdrawals.values() if w["status"] == "pending")
    paid = sum(w["amount_egp"] for w in MEMORY_WALLET_STORE.withdrawals.values() if w["status"] == "paid")
    assert sum(MEMORY_WALLET_STORE.balances.values()) + pending + paid == 500
    assert sum(e["amount_egp"] for e in MEMORY_WALLET_STORE.entries) == 500 - pending - paid


def test_notifications(reader, moderator) -> None:
    fund(reader, moderator, 300)
    withdrawal_id = withdraw(reader, 120).json()["id"]
    client.post(f"/api/v1/admin/wallet-withdrawals/{withdrawal_id}/paid", headers=moderator)

    kinds = [(n["kind"], n["data"].get("amount_egp")) for n in MEMORY_NOTIFICATION_STORE.notifications]
    assert ("withdrawal_requested", 120) in kinds
    assert ("withdrawal_paid", 120) in kinds
