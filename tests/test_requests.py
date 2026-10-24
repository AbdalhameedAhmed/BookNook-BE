from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_unauthenticated_users_cannot_request_listing() -> None:
    response = client.post("/api/v1/listings/listing-1/requests", json={"message": "Hi"})

    assert response.status_code == 401


def test_users_cannot_request_own_listing() -> None:
    response = client.post(
        "/api/v1/listings/listing-1/requests",
        json={"message": "Interested in buying."},
        headers={"Authorization": "Bearer demo-user-1"},
    )

    assert response.status_code == 403


def test_inactive_or_sold_listings_cannot_receive_requests() -> None:
    response = client.post(
        "/api/v1/listings/listing-4/requests",
        json={"message": "Interested."},
        headers={"Authorization": "Bearer demo-user-1"},
    )

    assert response.status_code == 400


def test_seller_can_accept_request_and_create_transaction() -> None:
    response = client.post(
        "/api/v1/listings/listing-2/requests",
        json={"message": "I want to exchange."},
        headers={"Authorization": "Bearer demo-user-1"},
    )

    assert response.status_code == 201
    request_id = response.json()["id"]

    accept_response = client.patch(
        f"/api/v1/requests/{request_id}",
        json={"status": "accepted"},
        headers={"Authorization": "Bearer demo-user-2"},
    )

    assert accept_response.status_code == 200
    assert accept_response.json()["status"] == "accepted"

    transactions_response = client.get(
        "/api/v1/me/transactions",
        headers={"Authorization": "Bearer demo-user-2"},
    )
    assert transactions_response.status_code == 200
    assert transactions_response.json()["items"]


def test_unauthorized_users_cannot_access_private_requests_or_transactions() -> None:
    requests_response = client.get("/api/v1/me/requests", headers={"Authorization": "Bearer demo-user-1"})
    transactions_response = client.get("/api/v1/me/transactions", headers={"Authorization": "Bearer demo-user-1"})

    assert requests_response.status_code == 200
    assert transactions_response.status_code == 200

    rejected = client.get("/api/v1/me/requests", headers={"Authorization": "Bearer demo-user-2"})
    assert rejected.status_code == 200
