from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_public_listings_endpoint_returns_active_items() -> None:
    response = client.get("/api/v1/listings")

    assert response.status_code == 200
    payload = response.json()
    assert payload["items"]
    assert payload["items"][0]["status"] == "active"
    assert payload["total"] >= 1


def test_listing_search_filters_by_title_or_author() -> None:
    response = client.get("/api/v1/listings", params={"q": "algebra"})

    assert response.status_code == 200
    payload = response.json()
    assert all(
        "algebra" in (item["title"] + " " + (item["author"] or "")).lower()
        for item in payload["items"]
    )


def test_listing_detail_endpoint_returns_listing() -> None:
    listing_id = client.get("/api/v1/listings").json()["items"][0]["id"]
    response = client.get(f"/api/v1/listings/{listing_id}")

    assert response.status_code == 200
    data = response.json()
    assert data["id"] == listing_id
    assert data["title"]
