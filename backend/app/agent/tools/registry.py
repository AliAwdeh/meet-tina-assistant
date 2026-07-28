import hashlib
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.repositories import records
from app.schemas.domain import ReminderCreate, TaskCreate
from app.services.audit import write_audit


@dataclass
class ToolContext:
    db: Session
    actor_type: str
    actor_id: str
    request_id: str


def _key(name: str, payload: BaseModel, actor_id: str) -> str:
    digest = hashlib.sha256(payload.model_dump_json().encode("utf-8")).hexdigest()
    return f"{name}:{actor_id}:{digest}"


def create_task_tool(context: ToolContext, payload: TaskCreate) -> dict[str, Any]:
    payload.model_validate(payload)
    task = records.create_task(context.db, payload, created_by=context.actor_id)
    write_audit(
        context.db,
        actor_type=context.actor_type,
        actor_id=context.actor_id,
        action="agent_create_task",
        entity_type="task",
        entity_id=task.id,
        safe_metadata={"idempotency_key": _key("create_task", payload, context.actor_id)},
    )
    return {"ok": True, "id": task.id, "retryable": False}


def create_reminder_tool(context: ToolContext, payload: ReminderCreate) -> dict[str, Any]:
    payload.model_validate(payload)
    reminder = records.create_reminder(context.db, payload)
    write_audit(
        context.db,
        actor_type=context.actor_type,
        actor_id=context.actor_id,
        action="agent_create_reminder",
        entity_type="reminder",
        entity_id=reminder.id,
        safe_metadata={"idempotency_key": _key("create_reminder", payload, context.actor_id)},
    )
    return {"ok": True, "id": reminder.id, "retryable": False}
