from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient
from sqlalchemy import select
from webauthn.helpers import bytes_to_base64url

from app.api.routes.auth import _coerce_utc
from app.auth.passwords import hash_password
from app.core.database import SessionLocal
from app.models.entities import PasskeyChallenge, User, UserPasskey


def _create_user() -> User:
    with SessionLocal() as db:
        user = User(
            name="Sami",
            email="sami@example.com",
            role="admin",
            password_hash=hash_password("super-secret-pass"),
        )
        db.add(user)
        db.commit()
        db.refresh(user)
        return user


def _login(client: TestClient) -> User:
    user = _create_user()
    response = client.post("/api/auth/login", json={"email": user.email, "password": "super-secret-pass"})
    assert response.status_code == 200
    return user


def test_passkey_registration_options_store_challenge(client: TestClient) -> None:
    user = _login(client)

    response = client.post("/api/auth/passkeys/register/options", json={})

    assert response.status_code == 200
    options = response.json()["options"]
    assert options["rp"]["name"] == "Meet Tina"
    assert options["rp"]["id"] == "localhost"
    assert options["user"]["name"] == "sami@example.com"
    assert options["authenticatorSelection"]["userVerification"] == "required"
    with SessionLocal() as db:
        challenge = db.scalar(select(PasskeyChallenge).where(PasskeyChallenge.user_id == user.id, PasskeyChallenge.purpose == "registration"))
        assert challenge is not None
        assert challenge.challenge == options["challenge"]


def test_passkey_expiry_comparison_accepts_sqlite_naive_datetime() -> None:
    naive_expiry = datetime.now(UTC).replace(tzinfo=None) + timedelta(minutes=1)

    assert _coerce_utc(naive_expiry).tzinfo is UTC


def test_passkey_login_options_require_registered_passkey(client: TestClient) -> None:
    _create_user()

    response = client.post("/api/auth/passkeys/login/options", json={"email": "sami@example.com"})

    assert response.status_code == 404
    assert "No Face ID/passkey" in response.json()["detail"]


def test_passkey_login_options_store_authentication_challenge(client: TestClient) -> None:
    user = _create_user()
    credential_id = bytes_to_base64url(b"test-credential-id")
    with SessionLocal() as db:
        db.add(
            UserPasskey(
                user_id=user.id,
                credential_id=credential_id,
                public_key=bytes_to_base64url(b"unused-in-options"),
                device_name="Sami iPhone",
            )
        )
        db.commit()

    response = client.post("/api/auth/passkeys/login/options", json={"email": user.email})

    assert response.status_code == 200
    options = response.json()["options"]
    assert options["rpId"] == "localhost"
    assert options["userVerification"] == "required"
    assert options["allowCredentials"][0]["id"] == credential_id
    with SessionLocal() as db:
        challenge = db.scalar(select(PasskeyChallenge).where(PasskeyChallenge.user_id == user.id, PasskeyChallenge.purpose == "authentication"))
        assert challenge is not None
        assert challenge.challenge == options["challenge"]
