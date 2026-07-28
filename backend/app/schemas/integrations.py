from typing import Any, Literal

from pydantic import BaseModel, EmailStr, Field


class EmailContact(BaseModel):
    email: EmailStr
    name: str | None = None


class N8nEmailPayload(BaseModel):
    request_id: str
    operation: Literal["send_email"] = "send_email"
    to: list[EmailContact]
    cc: list[EmailContact] = Field(default_factory=list)
    bcc: list[EmailContact] = Field(default_factory=list)
    subject: str
    html_body: str
    text_body: str
    related_person_ids: list[str] = Field(default_factory=list)
    related_task_ids: list[str] = Field(default_factory=list)
    related_meeting_id: str | None = None
    requested_by: str
    idempotency_key: str
    callback_url: str


class N8nCallback(BaseModel):
    request_id: str
    operation: Literal["send_email"]
    status: Literal["accepted", "sent", "failed", "retrying", "cancelled"]
    provider_message_id: str | None = None
    detail: str | None = None
    raw: dict[str, Any] = Field(default_factory=dict)


class OpenWAWebhookResponse(BaseModel):
    status: Literal["processed", "duplicate"]
    message_id: str
    reply: str | None = None
