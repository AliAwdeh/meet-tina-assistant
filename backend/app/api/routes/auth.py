from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Cookie, Depends, HTTPException, Response, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.dependencies import get_current_user, require_role
from app.auth.passwords import hash_password, hash_token, verify_password
from app.auth.tokens import create_access_token, create_refresh_token
from app.core.config import Settings, get_settings
from app.core.database import get_db
from app.models.entities import User, UserSession
from app.schemas.auth import CreateUserRequest, LoginRequest, TokenResponse, UserRead
from app.services.audit import write_audit

router = APIRouter()


def _user_read(user: User) -> UserRead:
    return UserRead(id=user.id, name=user.name, email=user.email, role=user.role, status=user.status)


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
    access_token = create_access_token(settings, user.id, user.role)
    refresh_token = create_refresh_token()
    session = UserSession(
        user_id=user.id,
        refresh_token_hash=hash_token(refresh_token),
        expires_at=datetime.now(UTC) + timedelta(days=settings.refresh_token_days),
    )
    user.last_login = datetime.now(UTC)
    db.add(session)
    write_audit(db, actor_type="dashboard_user", actor_id=user.id, action="login", entity_type="user", entity_id=user.id)
    db.commit()
    _set_auth_cookies(response, settings, access_token, refresh_token)
    return TokenResponse(access_token=access_token, user=_user_read(user))


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
    if session is None or session.revoked_at is not None or session.expires_at < datetime.now(UTC):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid refresh token")
    user = db.get(User, session.user_id)
    if user is None or user.status != "active":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Inactive or missing user")
    session.revoked_at = datetime.now(UTC)
    new_refresh = create_refresh_token()
    db.add(
        UserSession(
            user_id=user.id,
            refresh_token_hash=hash_token(new_refresh),
            expires_at=datetime.now(UTC) + timedelta(days=settings.refresh_token_days),
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
            session.revoked_at = datetime.now(UTC)
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
