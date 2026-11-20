"""Account settings: profile photo and email change."""

from datetime import datetime, timezone

import httpx
import pytest
from fastapi.testclient import TestClient

from app import main
from api.v1.endpoints import account, auth
from core.security import create_access_token, hash_password
from crud.crud_user import MEMORY_AUTH_STORE

client = TestClient(main.app)
PASSWORD = "correct horse battery"
PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 64


def make_user(email: str = "reader@example.com") -> tuple[str, dict[str, str]]:
    user = MEMORY_AUTH_STORE.register_user(email, hash_password(PASSWORD), "Reader")
    MEMORY_AUTH_STORE.users[user["id"]]["email_confirmed_at"] = datetime.now(timezone.utc)
    return user["id"], {"Authorization": f"Bearer {create_access_token(user['id'])[0]}"}


@pytest.fixture
def mailbox(monkeypatch: pytest.MonkeyPatch) -> dict[str, list]:
    """Captures the email-change link token and the notice to the old address."""
    sent: dict[str, list] = {"change": [], "notice": []}
    monkeypatch.setattr(account, "send_email_change_email", lambda to, token: sent["change"].append((to, token)))
    monkeypatch.setattr(auth, "send_email_changed_notice", lambda old, new: sent["notice"].append((old, new)))
    return sent


def change_email(headers: dict[str, str], new_email: str, password: str = PASSWORD):
    return client.post("/api/v1/me/email", headers=headers, json={"new_email": new_email, "current_password": password})


@pytest.mark.parametrize("method,path", [("post", "/api/v1/me/email"), ("delete", "/api/v1/me/avatar")])
def test_settings_require_sign_in(method: str, path: str) -> None:
    assert getattr(client, method)(path).status_code == 401
    assert client.post("/api/v1/me/avatar", files={"file": ("a.png", PNG, "image/png")}).status_code == 401


# --- Email change ---

def test_email_changes_only_after_the_new_inbox_confirms(mailbox) -> None:
    user_id, headers = make_user()

    response = change_email(headers, " New@Example.com ")
    assert response.status_code == 202, response.text
    assert MEMORY_AUTH_STORE.get_user(user_id)["email"] == "reader@example.com", "nothing changes before the link is opened"
    [(to, token)] = mailbox["change"]
    assert to == "new@example.com"

    confirmed = client.post("/api/v1/auth/confirm-email-change", json={"token": token})
    assert confirmed.status_code == 200, confirmed.text
    assert confirmed.json()["user"]["email"] == "new@example.com"
    assert mailbox["notice"] == [("reader@example.com", "new@example.com")]
    assert client.post("/api/v1/auth/login", json={"email": "new@example.com", "password": PASSWORD}).status_code == 200
    assert client.post("/api/v1/auth/login", json={"email": "reader@example.com", "password": PASSWORD}).status_code == 401
    assert client.post("/api/v1/auth/confirm-email-change", json={"token": token}).status_code == 400, "single use"


def test_email_change_needs_the_current_password_and_a_different_address(mailbox) -> None:
    _, headers = make_user()
    assert change_email(headers, "new@example.com", password="wrong password").status_code == 400
    assert change_email(headers, "reader@example.com").json()["detail"] == "That is already your email"
    assert change_email(headers, "not-an-email").status_code == 422
    assert mailbox["change"] == []


def test_taken_address_is_not_revealed_and_gets_no_email(mailbox) -> None:
    _, headers = make_user()
    make_user("taken@example.com")
    response = change_email(headers, "taken@example.com")
    assert response.status_code == 202
    assert response.json()["message"] == account.CHECK_NEW_INBOX
    assert mailbox["change"] == []


def test_address_registered_before_confirming_is_refused(mailbox) -> None:
    user_id, headers = make_user()
    change_email(headers, "new@example.com")
    make_user("new@example.com")
    [(_, token)] = mailbox["change"]

    assert client.post("/api/v1/auth/confirm-email-change", json={"token": token}).status_code == 409
    assert MEMORY_AUTH_STORE.get_user(user_id)["email"] == "reader@example.com"


def test_newer_request_cancels_older_link_and_old_reset_links_die(mailbox) -> None:
    user_id, headers = make_user()
    MEMORY_AUTH_STORE.issue_auth_token(user_id, "password_reset", "reset-hash",
                                       datetime(2999, 1, 1, tzinfo=timezone.utc))
    change_email(headers, "first@example.com")
    change_email(headers, "second@example.com")
    (_, first), (_, second) = mailbox["change"]

    assert client.post("/api/v1/auth/confirm-email-change", json={"token": first}).status_code == 400
    assert client.post("/api/v1/auth/confirm-email-change", json={"token": second}).status_code == 200
    assert MEMORY_AUTH_STORE.tokens["reset-hash"]["used_at"] is not None, "reset links sent to the old address stop working"


def test_email_change_is_rate_limited(mailbox) -> None:
    _, headers = make_user()
    codes = [change_email(headers, f"n{i}@example.com", password="wrong").status_code for i in range(6)]
    assert codes[:5] == [400] * 5 and codes[5] == 429


# --- Profile photo ---

@pytest.mark.parametrize("content,content_type,expected", [
    (b"GIF89a....", "image/gif", 415),
    (b"not really a png", "image/png", 415),
    (b"\x89PNG\r\n\x1a\n" + b"0" * (2 * 1024 * 1024), "image/png", 413),
])
def test_invalid_photos_are_rejected(content: bytes, content_type: str, expected: int) -> None:
    _, headers = make_user()
    response = client.post("/api/v1/me/avatar", headers=headers, files={"file": ("a", content, content_type)})
    assert response.status_code == expected


def test_photo_shows_on_the_account_and_can_be_removed() -> None:
    user_id, headers = make_user()
    MEMORY_AUTH_STORE.set_avatar_url(user_id, "https://cdn.example/a.png")
    assert client.get("/api/v1/auth/me", headers=headers).json()["avatar_url"] == "https://cdn.example/a.png"

    assert client.delete("/api/v1/me/avatar", headers=headers).status_code == 204
    assert client.get("/api/v1/auth/me", headers=headers).json()["avatar_url"] is None


def test_supabase_photo_upload_replaces_and_deletes_the_old_photo(monkeypatch: pytest.MonkeyPatch) -> None:
    from core.auth_settings import get_auth_settings
    from supabase_service import get_supabase_service
    from tests.test_supabase import sign_in_as

    user_id = "b797b8b4-09c5-4f7f-90d4-e89a595c6c4b"
    base = "https://project.example/storage/v1/object"
    old = f"{base}/public/book-images/avatars/{user_id}/old.png"
    monkeypatch.setenv("RFOOF_STORAGE", "supabase")
    monkeypatch.setenv("SUPABASE_URL", "https://project.example")
    monkeypatch.setenv("SUPABASE_SECRET_KEY", "sb_secret_test-key")
    monkeypatch.setenv("AUTH_JWT_SECRET", "test-secret-" + "x" * 40)
    get_supabase_service.cache_clear()
    get_auth_settings.cache_clear()
    headers = sign_in_as(monkeypatch, user_id)
    calls: list[tuple[str, str, object]] = []

    def fake_request(method: str, url: str, *, json=None, params=None, **_: object) -> httpx.Response:
        calls.append((method, url.rsplit("/", 1)[-1], json))
        return httpx.Response(200, json=[{"avatar_url": old}]) if method == "GET" else httpx.Response(204)

    monkeypatch.setattr(httpx, "request", fake_request)
    monkeypatch.setattr(httpx, "post", lambda url, **_: calls.append(("UPLOAD", url, None)) or httpx.Response(200))
    monkeypatch.setattr(httpx, "delete", lambda url, **_: calls.append(("DELETE", url, None)) or httpx.Response(200))
    try:
        response = client.post("/api/v1/me/avatar", headers=headers, files={"file": ("a.png", PNG, "image/png")})
    finally:
        get_supabase_service.cache_clear()
        get_auth_settings.cache_clear()

    assert response.status_code == 200, response.text
    new_url = response.json()["avatar_url"]
    assert new_url.startswith(f"{base}/public/book-images/avatars/{user_id}/") and new_url.endswith(".png")
    upload = next(c for c in calls if c[0] == "UPLOAD")
    assert upload[1].startswith(f"{base}/book-images/avatars/{user_id}/")
    patch = next(c for c in calls if c[0] == "PATCH")
    assert patch[1] == "profiles" and patch[2]["avatar_url"] == new_url
    assert ("DELETE", f"{base}/book-images/avatars/{user_id}/old.png", None) in calls
