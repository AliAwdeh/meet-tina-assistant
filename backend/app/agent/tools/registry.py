import hashlib
import uuid
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.integrations.n8n.client import N8nClient
from app.models.entities import Email, EmailRecipient, Person, Task
from app.repositories import records
from app.schemas.domain import ReminderCreate, TaskCreate
from app.schemas.integrations import EmailContact, N8nEmailPayload
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


async def send_email_tool(
    context: ToolContext,
    settings: Settings,
    *,
    to_people: list[Person],
    subject: str,
    text_body: str,
    related_task: Task | None = None,
) -> dict[str, Any]:
    recipients = [person for person in to_people if person.email]
    if not recipients:
        return {"ok": False, "retryable": False, "error": "missing_recipient_email"}
    request_id = str(uuid.uuid4())
    related_task_ids = [related_task.id] if related_task is not None else []
    idempotency_payload = EmailSendPayload(
        request_id=context.request_id,
        subject=subject,
        text_body=text_body,
        to=[p.email or "" for p in recipients],
    )
    payload = N8nEmailPayload(
        request_id=request_id,
        to=[EmailContact(email=person.email, name=person.full_name) for person in recipients if person.email],
        subject=subject,
        html_body="<p>" + text_body.replace("\n", "<br>") + "</p>",
        text_body=text_body,
        related_person_ids=[person.id for person in recipients],
        related_task_ids=related_task_ids,
        requested_by=context.actor_id,
        idempotency_key=_key("send_email", idempotency_payload, context.actor_id),
        callback_url=f"{settings.public_base_url.rstrip('/')}/api/integrations/n8n/callback",
    )
    email = Email(
        subject=subject,
        html_body=payload.html_body,
        text_body=text_body,
        status="queued",
        idempotency_key=payload.idempotency_key,
    )
    context.db.add(email)
    context.db.flush()
    for person in recipients:
        context.db.add(EmailRecipient(email_id=email.id, person_id=person.id, email_address=person.email or "", recipient_type="to"))
    n8n_request = await N8nClient(settings).send_email(context.db, payload)
    email.n8n_request_id = n8n_request.request_id
    email.status = n8n_request.status
    write_audit(
        context.db,
        actor_type=context.actor_type,
        actor_id=context.actor_id,
        action="agent_send_email",
        entity_type="email",
        entity_id=email.id,
        safe_metadata={"recipient_count": len(recipients), "related_task_ids": related_task_ids},
    )
    return {"ok": True, "id": email.id, "n8n_status": n8n_request.status, "retryable": n8n_request.status == "queued"}


class EmailSendPayload(BaseModel):
    request_id: str
    subject: str
    text_body: str
    to: list[str]
