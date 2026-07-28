from typing import Any

from sqlalchemy.orm import Session

from app.core.logging import request_id_var
from app.models.entities import AuditLog


def write_audit(
    db: Session,
    *,
    actor_type: str,
    action: str,
    entity_type: str,
    actor_id: str | None = None,
    entity_id: str | None = None,
    safe_metadata: dict[str, Any] | None = None,
) -> AuditLog:
    audit = AuditLog(
        actor_type=actor_type,
        actor_id=actor_id,
        action=action,
        entity_type=entity_type,
        entity_id=entity_id,
        request_id=request_id_var.get(),
        safe_metadata=safe_metadata or {},
    )
    db.add(audit)
    return audit
