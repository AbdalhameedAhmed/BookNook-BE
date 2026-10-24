from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_authenticated_user_can_report_listing() -> None:
    response = client.post(
        "/api/v1/reports",
        json={
            "listing_id": "listing-1",
            "reason": "inappropriate",
            "details": "Pricing looks incorrect.",
        },
        headers={"Authorization": "Bearer demo-user-2"},
    )

    assert response.status_code == 201
    assert response.json()["status"] == "open"
    assert response.json()["listing_id"] == "listing-1"


def test_transaction_can_be_completed_by_parties() -> None:
    create_response = client.post(
        "/api/v1/listings",
        json={
            "title": "History Final Revision Book",
            "author": "Mariam Adel",
            "condition": "good",
            "listing_type": "sale",
            "price_egp": 180,
            "area": "Fayoum Center",
            "meetup_preference": "direct_meetup",
        },
        headers={"Authorization": "Bearer demo-user-1"},
    )
    listing_id = create_response.json()["id"]

    request_response = client.post(
        f"/api/v1/listings/{listing_id}/requests",
        json={"message": "I would like to buy it."},
        headers={"Authorization": "Bearer demo-user-2"},
    )
    request_id = request_response.json()["id"]

    accept_response = client.patch(
        f"/api/v1/requests/{request_id}",
        json={"status": "accepted"},
        headers={"Authorization": "Bearer demo-user-1"},
    )
    transaction_id = next(
        transaction["id"]
        for transaction in client.get(
            "/api/v1/me/transactions",
            headers={"Authorization": "Bearer demo-user-2"},
        ).json()["items"]
        if transaction["listing_id"] == listing_id
    )

    complete_response = client.post(
        f"/api/v1/transactions/{transaction_id}/complete",
        headers={"Authorization": "Bearer demo-user-2"},
    )

    assert complete_response.status_code == 200
    assert complete_response.json()["status"] == "completed"

    listing_response = client.get(f"/api/v1/listings/{listing_id}")
    assert listing_response.json()["status"] == "sold"
