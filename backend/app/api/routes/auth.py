import json
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Cookie, Depends, HTTPException, Response, status
from sqlalchemy import delete, select
from sqlalchemy.orm import Session
from webauthn import (
    generate_authentication_options,
    generate_registration_options,
    options_to_json,
    verify_authentication_response,
    verify_registration_response,
)
from webauthn.helpers import base64url_to_bytes, bytes_to_base64url
from webauthn.helpers.exceptions import InvalidAuthenticationResponse, InvalidRegistrationResponse
from webauthn.helpers.structs import (
    AuthenticatorSelectionCriteria,
    PublicKeyCredentialDescriptor,
    ResidentKeyRequirement,
    UserVerificationRequirement,
)

from app.auth.dependencies import get_current_user, require_role
from app.auth.passwords import hash_password, hash_token, verify_password
from app.auth.tokens import create_access_token, create_refresh_token
from app.core.config import Settings, get_settings
from app.core.database import get_db
from app.models.entities import PasskeyChallenge, User, UserPasskey, UserSession
from app.schemas.auth import (
    CreateUserRequest,
    LoginRequest,
    PasskeyLoginOptionsRequest,
    PasskeyLoginVerifyRequest,
    PasskeyOptionsResponse,
    PasskeyRead,
    PasskeyRegisterVerifyRequest,
    TokenResponse,
    UserRead,
)
from app.services.audit import write_audit

router = APIRouter()
PASSKEY_CHALLENGE_MINUTES = 5


def _user_read(user: User) -> UserRead:
    return UserRead(id=user.id, name=user.name, email=user.email, role=user.role, status=user.status)


def _now_utc() -> datetime:
    return datetime.now(UTC)


def _coerce_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value


def _set_auth_cookies(response: Response, settings: Settings, access_token: str, refresh_token: str) -> None:
    response.set_cookie(
        "access_token",
        access_token,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="lax",
        max_age=settings.access_token_minutes * 60,
    )
    response.set_cookie(
        "refresh_token",
        refresh_token,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="lax",
        max_age=settings.refresh_token_days * 86400,
    )


def _issue_session(db: Session, response: Response, settings: Settings, user: User, action: str) -> TokenResponse:
    access_token = create_access_token(settings, user.id, user.role)
    refresh_token = create_refresh_token()
    session = UserSession(
        user_id=user.id,
        refresh_token_hash=hash_token(refresh_token),
        expires_at=_now_utc() + timedelta(days=settings.refresh_token_days),
    )
    user.last_login = _now_utc()
    db.add(session)
    write_audit(db, actor_type="dashboard_user", actor_id=user.id, action=action, entity_type="user", entity_id=user.id)
    db.commit()
    _set_auth_cookies(response, settings, access_token, refresh_token)
    return TokenResponse(access_token=access_token, user=_user_read(user))


def _passkey_options_dict(options: object) -> dict[str, object]:
    return json.loads(options_to_json(options))


def _credential_challenge(credential: dict[str, object]) -> str:
    response = credential.get("response")
    if not isinstance(response, dict):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid passkey response")
    client_data_json = response.get("clientDataJSON")
    if not isinstance(client_data_json, str):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid passkey client data")
    try:
        client_data = json.loads(base64url_to_bytes(client_data_json))
    except (ValueError, TypeError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid passkey client data") from exc
    challenge = client_data.get("challenge")
    if not isinstance(challenge, str):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid passkey challenge")
    return challenge


def _credential_id(credential: dict[str, object]) -> str:
    value = credential.get("rawId") or credential.get("id")
    if not isinstance(value, str) or not value:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid passkey credential")
    return value


def _store_passkey_challenge(db: Session, user_id: str, purpose: str, challenge: bytes) -> None:
    now = _now_utc()
    db.execute(delete(PasskeyChallenge).where(PasskeyChallenge.expires_at < now))
    db.add(
        PasskeyChallenge(
            user_id=user_id,
            purpose=purpose,
            challenge=bytes_to_base64url(challenge),
            expires_at=now + timedelta(minutes=PASSKEY_CHALLENGE_MINUTES),
        )
    )
    db.commit()


def _consume_passkey_challenge(db: Session, user_id: str, purpose: str, credential: dict[str, object]) -> bytes:
    challenge = _credential_challenge(credential)
    record = db.scalar(
        select(PasskeyChallenge).where(
            PasskeyChallenge.user_id == user_id,
            PasskeyChallenge.purpose == purpose,
            PasskeyChallenge.challenge == challenge,
        )
    )
    if record is None or _coerce_utc(record.expires_at) < _now_utc():
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Passkey challenge expired. Try again.")
    db.delete(record)
    db.commit()
    return base64url_to_bytes(challenge)


def _passkey_read(passkey: UserPasskey) -> PasskeyRead:
    return PasskeyRead(id=passkey.id, device_name=passkey.device_name, created_at=passkey.created_at, last_used_at=passkey.last_used_at)


@router.post("/login", response_model=TokenResponse)
def login(
    payload: LoginRequest,
    response: Response,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> TokenResponse:
    user = db.scalar(select(User).where(User.email == payload.email))
    if user is None or not verify_password(payload.password, user.password_hash) or user.status != "active":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid email or password")
    return _issue_session(db, response, settings, user, "login")


@router.get("/passkeys", response_model=list[PasskeyRead])
def list_passkeys(user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> list[PasskeyRead]:
    passkeys = db.scalars(select(UserPasskey).where(UserPasskey.user_id == user.id).order_by(UserPasskey.created_at.desc())).all()
    return [_passkey_read(passkey) for passkey in passkeys]


@router.post("/passkeys/register/options", response_model=PasskeyOptionsResponse)
def passkey_register_options(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> PasskeyOptionsResponse:
    existing = db.scalars(select(UserPasskey).where(UserPasskey.user_id == user.id)).all()
    options = generate_registration_options(
        rp_id=settings.effective_passkey_rp_id,
        rp_name=settings.passkey_rp_name,
        user_name=user.email,
        user_id=user.id.encode("utf-8"),
        user_display_name=user.name,
        authenticator_selection=AuthenticatorSelectionCriteria(
            resident_key=ResidentKeyRequirement.PREFERRED,
            require_resident_key=False,
            user_verification=UserVerificationRequirement.REQUIRED,
        ),
        exclude_credentials=[PublicKeyCredentialDescriptor(id=base64url_to_bytes(passkey.credential_id)) for passkey in existing],
    )
    _store_passkey_challenge(db, user.id, "registration", options.challenge)
    return PasskeyOptionsResponse(options=_passkey_options_dict(options))


@router.post("/passkeys/register/verify", response_model=PasskeyRead)
def passkey_register_verify(
    payload: PasskeyRegisterVerifyRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> PasskeyRead:
    expected_challenge = _consume_passkey_challenge(db, user.id, "registration", payload.credential)
    try:
        verified = verify_registration_response(
            credential=payload.credential,
            expected_challenge=expected_challenge,
            expected_rp_id=settings.effective_passkey_rp_id,
            expected_origin=settings.effective_passkey_origins,
            require_user_verification=True,
        )
    except InvalidRegistrationResponse as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Face ID/passkey setup failed. Try again.") from exc
    credential_id = bytes_to_base64url(verified.credential_id)
    if db.scalar(select(UserPasskey).where(UserPasskey.credential_id == credential_id)):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="This passkey is already registered.")
    response = payload.credential.get("response")
    transports = response.get("transports", []) if isinstance(response, dict) else []
    passkey = UserPasskey(
        user_id=user.id,
        credential_id=credential_id,
        public_key=bytes_to_base64url(verified.credential_public_key),
        sign_count=verified.sign_count,
        device_name=payload.device_name,
        transports=transports if isinstance(transports, list) else [],
    )
    db.add(passkey)
    write_audit(db, actor_type="dashboard_user", actor_id=user.id, action="register_passkey", entity_type="user_passkey", entity_id=passkey.id)
    db.commit()
    db.refresh(passkey)
    return _passkey_read(passkey)


@router.post("/passkeys/login/options", response_model=PasskeyOptionsResponse)
def passkey_login_options(
    payload: PasskeyLoginOptionsRequest,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> PasskeyOptionsResponse:
    user = db.scalar(select(User).where(User.email == payload.email))
    if user is None or user.status != "active":
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No active user found for that email.")
    passkeys = db.scalars(select(UserPasskey).where(UserPasskey.user_id == user.id)).all()
    if not passkeys:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No Face ID/passkey is set up for that email.")
    options = generate_authentication_options(
        rp_id=settings.effective_passkey_rp_id,
        user_verification=UserVerificationRequirement.REQUIRED,
        allow_credentials=[PublicKeyCredentialDescriptor(id=base64url_to_bytes(passkey.credential_id)) for passkey in passkeys],
    )
    _store_passkey_challenge(db, user.id, "authentication", options.challenge)
    return PasskeyOptionsResponse(options=_passkey_options_dict(options))


@router.post("/passkeys/login/verify", response_model=TokenResponse)
def passkey_login_verify(
    payload: PasskeyLoginVerifyRequest,
    response: Response,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> TokenResponse:
    user = db.scalar(select(User).where(User.email == payload.email))
    if user is None or user.status != "active":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid passkey login")
    passkey = db.scalar(
        select(UserPasskey).where(UserPasskey.user_id == user.id, UserPasskey.credential_id == _credential_id(payload.credential))
    )
    if passkey is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid passkey login")
    expected_challenge = _consume_passkey_challenge(db, user.id, "authentication", payload.credential)
    try:
        verified = verify_authentication_response(
            credential=payload.credential,
            expected_challenge=expected_challenge,
            expected_rp_id=settings.effective_passkey_rp_id,
            expected_origin=settings.effective_passkey_origins,
            credential_public_key=base64url_to_bytes(passkey.public_key),
            credential_current_sign_count=passkey.sign_count,
            require_user_verification=True,
        )
    except InvalidAuthenticationResponse as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid passkey login") from exc
    passkey.sign_count = verified.new_sign_count
    passkey.last_used_at = _now_utc()
    write_audit(db, actor_type="dashboard_user", actor_id=user.id, action="passkey_login", entity_type="user_passkey", entity_id=passkey.id)
    return _issue_session(db, response, settings, user, "passkey_session")


@router.post("/refresh", response_model=TokenResponse)
def refresh(
    response: Response,
    refresh_token: str | None = Cookie(default=None, alias="refresh_token"),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> TokenResponse:
    if not refresh_token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing refresh token")
    session = db.scalar(select(UserSession).where(UserSession.refresh_token_hash == hash_token(refresh_token)))
    if session is None or session.revoked_at is not None or _coerce_utc(session.expires_at) < _now_utc():
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid refresh token")
    user = db.get(User, session.user_id)
    if user is None or user.status != "active":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Inactive or missing user")
    session.revoked_at = _now_utc()
    new_refresh = create_refresh_token()
    db.add(
        UserSession(
            user_id=user.id,
            refresh_token_hash=hash_token(new_refresh),
            expires_at=_now_utc() + timedelta(days=settings.refresh_token_days),
        )
    )
    access_token = create_access_token(settings, user.id, user.role)
    write_audit(db, actor_type="dashboard_user", actor_id=user.id, action="refresh_session", entity_type="user", entity_id=user.id)
    db.commit()
    _set_auth_cookies(response, settings, access_token, new_refresh)
    return TokenResponse(access_token=access_token, user=_user_read(user))


@router.post("/logout")
def logout(
    response: Response,
    refresh_token: str | None = Cookie(default=None, alias="refresh_token"),
    db: Session = Depends(get_db),
) -> dict[str, str]:
    if refresh_token:
        session = db.scalar(select(UserSession).where(UserSession.refresh_token_hash == hash_token(refresh_token)))
        if session and session.revoked_at is None:
            session.revoked_at = _now_utc()
            write_audit(
                db,
                actor_type="dashboard_user",
                actor_id=session.user_id,
                action="logout",
                entity_type="user",
                entity_id=session.user_id,
            )
            db.commit()
    response.delete_cookie("access_token")
    response.delete_cookie("refresh_token")
    return {"status": "logged_out"}


@router.get("/me", response_model=UserRead)
def me(user: User = Depends(get_current_user)) -> UserRead:
    return _user_read(user)


@router.post("/users", response_model=UserRead, dependencies=[Depends(require_role("admin"))])
def create_user(payload: CreateUserRequest, db: Session = Depends(get_db)) -> UserRead:
    if db.scalar(select(User).where(User.email == payload.email)):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Email already exists")
    user = User(name=payload.name, email=payload.email, role=payload.role, password_hash=hash_password(payload.password))
    db.add(user)
    write_audit(
        db,
        actor_type="dashboard_user",
        action="create_user",
        entity_type="user",
        entity_id=user.id,
        safe_metadata={"role": user.role},
    )
    db.commit()
    db.refresh(user)
    return _user_read(user)
