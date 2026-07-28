from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, EmailStr, Field, field_validator

Priority = Literal["low", "medium", "high", "urgent"]


class PersonCreate(BaseModel):
    full_name: str = Field(min_length=1, max_length=255)
    company: str | None = None
    job_title: str | None = None
    email: EmailStr | None = None
    phone_number: str | None = None
    whatsapp_number: str | None = None
    notes: str | None = None
    preferred_language: str | None = None
    timezone: str | None = None


class PersonRead(PersonCreate):
    id: str
    active: bool
    created_at: datetime
    updated_at: datetime


class TaskCreate(BaseModel):
    title: str = Field(min_length=1, max_length=255)
    description: str | None = None
    priority: Priority = "medium"
    assigned_person_id: str | None = None
    due_date: datetime | None = None
    related_meeting_id: str | None = None


class TaskRead(TaskCreate):
    id: str
    status: str
    completed_at: datetime | None
    created_at: datetime
    updated_at: datetime


class MeetingCreate(BaseModel):
    title: str = Field(min_length=1, max_length=255)
    description: str | None = None
    start_time: datetime
    end_time: datetime | None = None
    timezone: str = "UTC"
    location_or_url: str | None = None
    preparation_offset_hours: int = Field(default=4, ge=0, le=168)
    participant_ids: list[str] = Field(default_factory=list)


class MeetingRead(MeetingCreate):
    id: str
    status: str
    preparation_status: str
    created_at: datetime
    updated_at: datetime


class ReminderCreate(BaseModel):
    title: str = Field(min_length=1, max_length=255)
    description: str | None = None
    trigger_time: datetime
    timezone: str = "UTC"
    delivery_channel: Literal["whatsapp", "dashboard"] = "whatsapp"
    related_task_id: str | None = None
    related_meeting_id: str | None = None
    related_person_id: str | None = None


class ReminderRead(ReminderCreate):
    id: str
    status: str
    retry_count: int
    executed_at: datetime | None
    created_at: datetime
    updated_at: datetime


class DashboardSummary(BaseModel):
    today_meetings: int
    upcoming_meetings: int
    due_tasks: int
    overdue_tasks: int
    pending_reminders: int
    recent_messages: int
    pending_email_approvals: int
    failed_integrations: int
    scheduler_health: str


class NormalizedMessage(BaseModel):
    external_message_id: str
    conversation_id: str
    sender_phone: str
    sender_name: str | None = None
    message_type: Literal["text", "voice", "image", "document", "location", "other"]
    text: str | None = None
    media_path: str | None = None
    mime_type: str | None = None
    timestamp: datetime
    raw_event: dict[str, Any]

    @field_validator("sender_phone")
    @classmethod
    def simple_phone_guard(cls, value: str) -> str:
        cleaned = "".join(ch for ch in value if ch.isdigit() or ch == "+")
        return cleaned[:32]
