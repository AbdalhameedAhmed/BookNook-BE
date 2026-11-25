"""Listings record one of Egypt's governorates and the catalogue can be filtered by it."""
from fastapi.testclient import TestClient

from main import app

client = TestClient(app)
AUTH = {"Authorization": "Bearer demo-user-1"}


def _payload(**overrides: object) -> dict[str, object]:
    return {
        "title": "Nationwide book", "condition": "good", "listing_type": "free",
        "governorate": "alexandria", "area": "Smouha", **overrides,
    }


def test_create_listing_requires_governorate() -> None:
    payload = _payload()
    del payload["governorate"]
    assert client.post("/api/v1/listings", json=payload, headers=AUTH).status_code == 422


def test_create_listing_rejects_unknown_governorate() -> None:
    response = client.post("/api/v1/listings", json=_payload(governorate="atlantis"), headers=AUTH)
    assert response.status_code == 400


def test_catalogue_filters_by_governorate() -> None:
    created = client.post("/api/v1/listings", json=_payload(title="Alexandria only"), headers=AUTH)
    assert created.status_code == 201 and created.json()["governorate"] == "alexandria"

    alexandria = client.get("/api/v1/listings", params={"governorate": "alexandria", "page_size": 100}).json()["items"]
    assert alexandria and all(item["governorate"] == "alexandria" for item in alexandria)

    fayoum = client.get("/api/v1/listings", params={"governorate": "fayoum", "page_size": 100}).json()["items"]
    assert all(item["title"] != "Alexandria only" for item in fayoum)


def test_catalogue_rejects_unknown_governorate_filter() -> None:
    assert client.get("/api/v1/listings", params={"governorate": "atlantis"}).status_code == 400


def test_governorate_cannot_be_changed_to_invalid_or_removed() -> None:
    listing_id = client.post("/api/v1/listings", json=_payload(), headers=AUTH).json()["id"]
    url = f"/api/v1/listings/{listing_id}"
    assert client.patch(url, json={"governorate": "atlantis"}, headers=AUTH).status_code == 400
    assert client.patch(url, json={"governorate": None}, headers=AUTH).status_code == 400
    moved = client.patch(url, json={"governorate": "cairo"}, headers=AUTH)
    assert moved.status_code == 200 and moved.json()["governorate"] == "cairo"
