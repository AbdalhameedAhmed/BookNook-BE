from fastapi.testclient import TestClient

from app import main
from app.main import app

client = TestClient(app)


def test_book_photo_larger_than_two_mb_is_rejected() -> None:
    content = b"\x89PNG\r\n\x1a\n" + b"0" * main.MAX_BOOK_IMAGE_SIZE_BYTES

    response = client.post(
        "/api/v1/listing-images",
        headers={"Authorization": "Bearer demo-user-1"},
        files={"file": ("book.png", content, "image/png")},
    )

    assert response.status_code == 413


def test_unauthenticated_users_cannot_upload_book_photos() -> None:
    response = client.post(
        "/api/v1/listing-images",
        files={"file": ("book.png", b"\x89PNG\r\n\x1a\nsmall image", "image/png")},
    )

    assert response.status_code == 401


def test_book_photo_rejects_mismatched_image_content() -> None:
    response = client.post(
        "/api/v1/listing-images",
        headers={"Authorization": "Bearer demo-user-1"},
        files={"file": ("book.png", b"not an image", "image/png")},
    )

    assert response.status_code == 415


def test_unauthenticated_users_cannot_create_listing() -> None:
    payload = {
        "title": "Fiction Writing",
        "author": "Huda Samir",
        "condition": "good",
        "listing_type": "sale",
        "price_egp": 250,
        "area": "Fayoum Center",
        "meetup_preference": "direct_meetup",
    }

    response = client.post("/api/v1/listings", json=payload)

    assert response.status_code == 401


def test_user_cannot_edit_another_users_listing() -> None:
    create_response = client.post(
        "/api/v1/listings",
        json={
            "title": "Organic Chemistry",
            "author": "Ramy Nabil",
            "condition": "good",
            "listing_type": "sale",
            "price_egp": 300,
            "area": "Fayoum Center",
            "meetup_preference": "direct_meetup",
        },
        headers={"Authorization": "Bearer demo-user-1"},
    )
    listing_id = create_response.json()["id"]

    response = client.patch(
        f"/api/v1/listings/{listing_id}",
        json={"title": "Updated title"},
        headers={"Authorization": "Bearer demo-user-2"},
    )

    assert response.status_code == 403


def test_sale_listings_require_valid_price() -> None:
    response = client.post(
        "/api/v1/listings",
        json={
            "title": "Physics Workbook",
            "author": "Iman Ahmed",
            "condition": "acceptable",
            "listing_type": "sale",
            "area": "Fayoum Center",
            "meetup_preference": "direct_meetup",
        },
        headers={"Authorization": "Bearer demo-user-1"},
    )

    assert response.status_code == 400
    assert "price_egp" in response.json()["detail"]


def test_exchange_and_free_listings_do_not_require_sale_price() -> None:
    for listing_type in ("exchange", "free"):
        response = client.post(
            "/api/v1/listings",
            json={
                "title": f"Book type {listing_type}",
                "author": "Amina Ali",
                "condition": "like_new",
                "listing_type": listing_type,
                "area": "Tamiyya",
                "meetup_preference": "pickup_point",
            },
            headers={"Authorization": "Bearer demo-user-2"},
        )

        assert response.status_code == 201


def test_owner_can_mark_sale_listing_sold(monkeypatch) -> None:
    monkeypatch.setattr(main, "LISTINGS", [dict(item) for item in main.LISTINGS])
    response = client.post(
        "/api/v1/listings/listing-1/mark-complete",
        headers={"Authorization": "Bearer demo-user-1"},
    )

    assert response.status_code == 200
    assert response.json()["status"] == "sold"


def test_non_owner_cannot_mark_listing_complete() -> None:
    response = client.post(
        "/api/v1/listings/listing-1/mark-complete",
        headers={"Authorization": "Bearer demo-user-2"},
    )

    assert response.status_code == 403
