from datetime import datetime, timedelta, timezone

import bcrypt
import jwt
import pytest
from fastapi.testclient import TestClient

from app import main
from api.v1.endpoints import auth as auth_endpoints
from core.auth_settings import get_auth_settings
from core.security import JWT_ALGORITHM, JWT_ISSUER, verify_password
from crud.crud_user import MEMORY_AUTH_STORE

client = TestClient(main.app)
EMAIL = "reader@example.com"
PASSWORD = "correct horse battery"


@pytest.fixture
def outbox(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, str, str]]:
    """Captures (kind, recipient, raw token) instead of sending email."""
    sent: list[tuple[str, str, str]] = []
    monkeypatch.setattr(auth_endpoints, "send_confirmation_email", lambda to, token: sent.append(("confirm", to, token)))
    monkeypatch.setattr(auth_endpoints, "send_password_reset_email", lambda to, token: sent.append(("reset", to, token)))
    return sent


def sign_up(email: str = EMAIL, password: str = PASSWORD) -> None:
    response = client.post("/api/v1/auth/signup", json={"email": email, "password": password, "display_name": "Mona"})
    assert response.status_code == 202


def confirmed_session(outbox: list[tuple[str, str, str]], email: str = EMAIL) -> dict[str, object]:
    sign_up(email)
    response = client.post("/api/v1/auth/confirm-email", json={"token": outbox[-1][2]})
    assert response.status_code == 200
    return response.json()


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_signup_sends_confirmation_and_blocks_login_until_confirmed(outbox) -> None:
    sign_up("  Reader@Example.com ")

    assert [(kind, to) for kind, to, _ in outbox] == [("confirm", EMAIL)]
    login = client.post("/api/v1/auth/login", json={"email": EMAIL, "password": PASSWORD})
    assert login.status_code == 403
    assert login.json()["detail"] == "Email not confirmed"


def test_password_is_stored_as_bcrypt_hash_not_plaintext(outbox) -> None:
    sign_up()

    stored = MEMORY_AUTH_STORE.find_user_by_email(EMAIL)
    assert stored["password_hash"] != PASSWORD
    assert stored["password_hash"].startswith("$2b$")
    assert verify_password(PASSWORD, stored["password_hash"])


def test_confirm_email_signs_in_and_token_cannot_be_reused(outbox) -> None:
    session = confirmed_session(outbox)

    assert session["token_type"] == "bearer"
    assert session["user"]["email"] == EMAIL
    assert session["user"]["display_name"] == "Mona"
    assert session["user"]["role"] == "user"
    reused = client.post("/api/v1/auth/confirm-email", json={"token": outbox[-1][2]})
    assert reused.status_code == 400


def test_login_returns_token_that_authorizes_private_endpoints(outbox) -> None:
    confirmed_session(outbox)

    login = client.post("/api/v1/auth/login", json={"email": EMAIL.upper(), "password": PASSWORD})
    assert login.status_code == 200
    token = login.json()["access_token"]

    me = client.get("/api/v1/auth/me", headers=bearer(token))
    assert me.status_code == 200
    assert me.json()["email"] == EMAIL

    created = client.post(
        "/api/v1/listings",
        headers=bearer(token),
        json={"title": "Backend Auth Book", "condition": "good", "listing_type": "free", "area": "Fayoum Center"},
    )
    assert created.status_code == 201
    assert created.json()["seller_id"] == me.json()["id"]


def test_wrong_password_and_unknown_email_get_the_same_error(outbox) -> None:
    confirmed_session(outbox)

    wrong_password = client.post("/api/v1/auth/login", json={"email": EMAIL, "password": "not the password"})
    unknown_email = client.post("/api/v1/auth/login", json={"email": "nobody@example.com", "password": PASSWORD})

    assert wrong_password.status_code == unknown_email.status_code == 401
    assert wrong_password.json() == unknown_email.json() == {"detail": "Invalid login credentials"}


def test_duplicate_signup_does_not_reveal_or_overwrite_existing_account(outbox) -> None:
    confirmed_session(outbox)
    emails_before = len(outbox)

    response = client.post(
        "/api/v1/auth/signup", json={"email": EMAIL, "password": "attacker password", "display_name": "Eve"}
    )

    assert response.status_code == 202
    assert len(outbox) == emails_before
    assert client.post("/api/v1/auth/login", json={"email": EMAIL, "password": PASSWORD}).status_code == 200


def test_forgot_password_for_unknown_email_looks_identical_and_sends_nothing(outbox) -> None:
    known = confirmed_session(outbox)
    assert known
    emails_before = len(outbox)

    unknown = client.post("/api/v1/auth/forgot-password", json={"email": "nobody@example.com"})
    existing = client.post("/api/v1/auth/forgot-password", json={"email": EMAIL})

    assert unknown.status_code == existing.status_code == 202
    assert unknown.json() == existing.json()
    assert [kind for kind, _, _ in outbox[emails_before:]] == ["reset"]


def test_password_reset_changes_password_and_revokes_older_tokens(outbox) -> None:
    session = confirmed_session(outbox)
    user_id = session["user"]["id"]
    MEMORY_AUTH_STORE.users[user_id]["password_changed_at"] = datetime.now(timezone.utc) - timedelta(minutes=10)
    older_token = jwt.encode(
        {
            "sub": user_id,
            "iss": JWT_ISSUER,
            "typ": "access",
            "iat": int((datetime.now(timezone.utc) - timedelta(minutes=5)).timestamp()),
            "exp": int((datetime.now(timezone.utc) + timedelta(hours=1)).timestamp()),
        },
        get_auth_settings().jwt_secret,
        algorithm=JWT_ALGORITHM,
    )
    assert client.get("/api/v1/auth/me", headers=bearer(older_token)).status_code == 200

    client.post("/api/v1/auth/forgot-password", json={"email": EMAIL})
    reset = client.post("/api/v1/auth/reset-password", json={"token": outbox[-1][2], "password": "a brand new secret"})

    assert reset.status_code == 200
    assert client.get("/api/v1/auth/me", headers=bearer(older_token)).status_code == 401
    assert client.get("/api/v1/auth/me", headers=bearer(reset.json()["access_token"])).status_code == 200
    assert client.post("/api/v1/auth/login", json={"email": EMAIL, "password": PASSWORD}).status_code == 401
    assert client.post("/api/v1/auth/login", json={"email": EMAIL, "password": "a brand new secret"}).status_code == 200
    reused = client.post("/api/v1/auth/reset-password", json={"token": outbox[-1][2], "password": "another secret!"})
    assert reused.status_code == 400


def test_expired_reset_link_is_rejected(outbox) -> None:
    confirmed_session(outbox)
    client.post("/api/v1/auth/forgot-password", json={"email": EMAIL})
    for token in MEMORY_AUTH_STORE.tokens.values():
        token["expires_at"] = datetime.now(timezone.utc) - timedelta(seconds=1)

    response = client.post("/api/v1/auth/reset-password", json={"token": outbox[-1][2], "password": "a brand new secret"})

    assert response.status_code == 400


@pytest.mark.parametrize(
    "token",
    [
        "not-a-jwt",
        jwt.encode({"sub": "x", "iss": JWT_ISSUER, "typ": "access", "iat": 0, "exp": 9999999999}, "y" * 40, algorithm="HS256"),
    ],
)
def test_invalid_or_foreign_tokens_are_rejected(token: str) -> None:
    assert client.get("/api/v1/auth/me", headers=bearer(token)).status_code == 401
    assert client.get("/api/v1/me/listings", headers=bearer(token)).status_code == 401


def test_expired_access_token_is_rejected(outbox) -> None:
    user_id = confirmed_session(outbox)["user"]["id"]
    expired = jwt.encode(
        {"sub": user_id, "iss": JWT_ISSUER, "typ": "access", "iat": 1, "exp": 2},
        get_auth_settings().jwt_secret,
        algorithm=JWT_ALGORITHM,
    )

    assert client.get("/api/v1/auth/me", headers=bearer(expired)).status_code == 401


def test_me_requires_authentication() -> None:
    assert client.get("/api/v1/auth/me").status_code == 401


@pytest.mark.parametrize("password", ["short", "x" * 73])
def test_signup_rejects_weak_or_overlong_passwords(password: str, outbox) -> None:
    response = client.post(
        "/api/v1/auth/signup", json={"email": EMAIL, "password": password, "display_name": "Mona"}
    )

    assert response.status_code == 422
    assert outbox == []


def test_repeated_failed_logins_are_rate_limited(outbox) -> None:
    confirmed_session(outbox)

    statuses = [
        client.post("/api/v1/auth/login", json={"email": EMAIL, "password": "wrong password"}).status_code
        for _ in range(11)
    ]

    assert statuses[:10] == [401] * 10
    assert statuses[10] == 429


def test_password_hashes_copied_from_supabase_auth_still_verify() -> None:
    supabase_style_hash = bcrypt.hashpw(PASSWORD.encode(), bcrypt.gensalt(rounds=10, prefix=b"2a")).decode()

    assert supabase_style_hash.startswith("$2a$10$")
    assert verify_password(PASSWORD, supabase_style_hash)
    assert not verify_password("wrong", supabase_style_hash)
    assert not verify_password(PASSWORD, None)
