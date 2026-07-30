from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

Intent = Literal[
    "general_conversation",
    "create_task",
    "update_task",
    "complete_task",
    "assign_responsibility",
    "set_person_goal",
    "create_reminder",
    "create_meeting",
    "update_meeting",
    "cancel_meeting",
    "add_meeting_notes",
    "prepare_meeting_brief",
    "send_email",
    "schedule_email",
    "create_follow_up",
    "record_person_note",
    "analyze_image",
    "transcribe_voice_note",
    "process_document",
    "query_records",
]


class ClassificationResult(BaseModel):
    intent: Intent
    confidence: float = Field(ge=0.0, le=1.0)
    requires_confirmation: bool = False
    rationale: str = ""


class ExtractedAction(BaseModel):
    action_type: Literal[
        "create_task",
        "update_task",
        "complete_task",
        "create_reminder",
        "create_meeting",
        "assign_goal",
        "send_email",
        "schedule_email",
        "upsert_person",
        "record_person_note",
        "query_records",
        "no_action",
    ]
    title: str | None = None
    description: str | None = None
    person_names: list[str] = Field(default_factory=list)
    person_emails: list[str] = Field(default_factory=list)
    subject: str | None = None
    body: str | None = None
    related_task_id: str | None = None
    project_name: str | None = None
    project_id: str | None = None
    query_target: Literal["summary", "people", "tasks", "emails", "meetings", "reminders"] | None = None
    target_text: str | None = None
    due_at: datetime | None = None
    meeting_at: datetime | None = None
    priority: Literal["low", "medium", "high", "urgent"] | None = None
    confidence: float = Field(ge=0.0, le=1.0, default=0.0)
    missing_fields: list[str] = Field(default_factory=list)


class AgentResult(BaseModel):
    reply: str
    classification: ClassificationResult
    actions: list[ExtractedAction]
    persisted_entity_ids: list[str] = Field(default_factory=list)
    requires_confirmation: bool = False
