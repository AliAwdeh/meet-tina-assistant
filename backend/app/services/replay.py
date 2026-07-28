from datetime import UTC, datetime, timedelta

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.entities import ReplayGuard


def claim_replay_key(db: Session, *, source: str, key: str, ttl_seconds: int) -> bool:
    now = datetime.now(UTC)
    db.query(ReplayGuard).filter(ReplayGuard.expires_at < now).delete()
    guard = ReplayGuard(key=f"{source}:{key}", source=source, expires_at=now + timedelta(seconds=ttl_seconds))
    db.add(guard)
    try:
        db.flush()
        return True
    except IntegrityError:
        db.rollback()
        return False
