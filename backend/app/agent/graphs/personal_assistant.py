import logging
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, TypedDict

from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.graph import END, StateGraph
from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.agent.tools.registry import ToolContext, create_reminder_tool, create_task_tool, send_email_tool
from app.core.config import Settings
from app.integrations.ai.client import chat_model, structured_json
from app.models.entities import Conversation, Email, EmailRecipient, Meeting, Message, Person, Project, Reminder, Task
from app.schemas.agent import AgentResult, ClassificationResult, ExtractedAction
from app.schemas.domain import NormalizedMessage, PersonCreate, ReminderCreate, TaskCreate

logger = logging.getLogger(__name__)
EMAIL_RE = re.compile(r"(?P<email>[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,})", re.IGNORECASE)


class AssistantState(TypedDict, total=False):
    settings: Settings
    db: Session
    message: NormalizedMessage
    classification: ClassificationResult
    actions: list[ExtractedAction]
    persisted_entity_ids: list[str]
    conversation: Conversation | None
    last_person: Person | None
    last_task: Task | None
    referenced_people: list[Person]
    referenced_tasks: list[Task]
    referenced_project: Project | None
    recent_messages: list[Message]
    read_result: str | None
    tool_errors: list[str]
    reply: str


def _title_case_name(value: str) -> str:
    cleaned = re.sub(r"\b(email|e-mail|mail|is|at|for|the|a|an)\b", " ", value, flags=re.IGNORECASE)
    cleaned = re.sub(r"[^A-Za-zÀ-ÿ' -]", " ", cleaned)
    words = [word for word in cleaned.split() if len(word) > 1]
    if not words:
        return "Contact"
    return " ".join(word[:1].upper() + word[1:].lower() for word in words[-4:])


def _name_from_email(email: str) -> str:
    local = email.split("@", 1)[0]
    return _title_case_name(local.replace(".", " ").replace("_", " ").replace("-", " "))


def _extract_people_from_text(text: str) -> list[PersonCreate]:
    people: list[PersonCreate] = []
    for match in EMAIL_RE.finditer(text):
        email = match.group("email")
        prefix = text[max(0, match.start() - 80) : match.start()]
        name = _title_case_name(prefix) if prefix.strip() else _name_from_email(email)
        if name.lower() in {"contact", "email"}:
            name = _name_from_email(email)
        people.append(PersonCreate(full_name=name, email=email))
    return people


def _upsert_person(db: Session, payload: PersonCreate) -> Person:
    person = None
    if payload.email:
        person = db.scalar(select(Person).where(Person.email == str(payload.email)))
    if person is None:
        person = db.scalar(select(Person).where(Person.full_name.ilike(payload.full_name)))
    if person is None:
        person = Person(**payload.model_dump())
        db.add(person)
        db.flush()
        return person
    person.full_name = payload.full_name or person.full_name
    if payload.email and not person.email:
        person.email = str(payload.email)
    db.flush()
    return person


def _query_tokens(text: str) -> list[str]:
    ignored = {"send", "email", "task", "tasks"}
    return [token.lower() for token in re.findall(r"[A-Za-z][A-Za-z'-]{1,}", text) if token.lower() not in ignored]


def _find_referenced_people(db: Session, text: str, last_person: Person | None = None, limit: int = 10) -> list[Person]:
    found: list[Person] = []
    seen: set[str] = set()
    for payload in _extract_people_from_text(text):
        person = db.scalar(select(Person).where(Person.email == str(payload.email))) if payload.email else None
        if person and person.id not in seen:
            found.append(person)
            seen.add(person.id)
    for token in _query_tokens(text):
        if len(token) < 3:
            continue
        matches = db.scalars(select(Person).where(Person.active.is_(True), Person.full_name.ilike(f"%{token}%")).limit(limit)).all()
        for person in matches:
            if person.id not in seen:
                found.append(person)
                seen.add(person.id)
    if not found and any(word in text.lower().split() for word in ["him", "her", "them", "that"]) and last_person is not None:
        found.append(last_person)
    return found[:limit]


def _find_referenced_tasks(db: Session, text: str, people: list[Person], last_task: Task | None = None, limit: int = 10) -> list[Task]:
    statuses = ("open", "pending", "in_progress")
    stmt = select(Task).where(Task.status.in_(statuses)).order_by(Task.created_at.desc()).limit(limit)
    if people:
        stmt = stmt.where(Task.assigned_person_id.in_([person.id for person in people]))
    tasks = list(db.scalars(stmt))
    if not tasks and last_task is not None:
        tasks.append(last_task)
    tokens = [token for token in _query_tokens(text) if len(token) > 3]
    if tokens and not people:
        matching = []
        for task in tasks:
            haystack = f"{task.title} {task.description or ''}".lower()
            if any(token in haystack for token in tokens):
                matching.append(task)
        if matching:
            tasks = matching
    return tasks[:limit]


def _conversation_context(
    db: Session,
    message: NormalizedMessage,
) -> tuple[Conversation | None, Person | None, Task | None, Project | None]:
    conversation = db.scalar(select(Conversation).where(Conversation.whatsapp_chat_id == message.conversation_id))
    state = conversation.state if conversation and conversation.state else {}
    last_person = db.get(Person, state.get("last_person_id")) if state.get("last_person_id") else None
    last_task = db.get(Task, state.get("last_task_id")) if state.get("last_task_id") else None
    last_project = db.get(Project, state.get("last_project_id")) if state.get("last_project_id") else None
    if last_task is None and conversation is not None:
        message_ids = select(Message.id).where(Message.conversation_id == conversation.id)
        last_task = db.scalar(select(Task).where(Task.source_message_id.in_(message_ids)).order_by(Task.created_at.desc()))
    if last_project is None and last_task is not None and last_task.project_id:
        last_project = db.get(Project, last_task.project_id)
    return conversation, last_person, last_task, last_project


def _task_title(text: str) -> str:
    match = re.search(r"\bneeds?\s+to\s+(?P<title>.+)", text, flags=re.IGNORECASE)
    if match:
        title = re.sub(r"\b(urgent|high priority|medium priority|low priority)\b", "", match.group("title"), flags=re.IGNORECASE)
        title = re.sub(r"\s+", " ", title).strip(" .,-")
        return title[:255] or "WhatsApp task"
    return text.strip()[:255] or "WhatsApp task"


def _extract_project_name(text: str) -> str | None:
    patterns = [
        r"\bproject\s+(?P<name>[A-Za-z0-9][A-Za-z0-9 &._'-]{1,80})",
        r"\bfor\s+(?P<name>[A-Za-z0-9][A-Za-z0-9 &._'-]{1,80})\s+project\b",
        r"\bon\s+(?P<name>[A-Za-z0-9][A-Za-z0-9 &._'-]{1,80})\s+project\b",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if not match:
            continue
        name = re.split(r"\b(needs?|task|priority|due|send|email|remind)\b", match.group("name"), flags=re.IGNORECASE)[0]
        name = re.sub(r"\s+", " ", name).strip(" .,-")
        if name:
            return name[:255]
    return None


def _upsert_project(db: Session, person: Person, name: str) -> Project:
    project = db.scalar(select(Project).where(Project.person_id == person.id, Project.name.ilike(name)))
    if project is None:
        project = Project(person_id=person.id, name=name)
        db.add(project)
        db.flush()
    return project


def _email_body(person: Person | None, task: Task | None, original_text: str) -> str:
    greeting = f"Hi {person.full_name}," if person else "Hi,"
    if task is not None:
        body = f"{greeting}\n\nSharing this task with you:\n\n{task.title}"
        if task.description and task.description != task.title:
            body += f"\n\nContext: {task.description}"
        body += "\n\nThanks."
        return body
    return f"{greeting}\n\n{original_text.strip()}\n\nThanks."


def _heuristic_action(state: AssistantState) -> ExtractedAction:
    message = state["message"]
    text = (message.text or "").strip()
    lowered = text.lower()
    last_person = state.get("last_person")
    last_task = state.get("last_task")
    extracted_people = _extract_people_from_text(text)
    person_names = [person.full_name for person in extracted_people]
    person_emails = [str(person.email) for person in extracted_people if person.email]
    project_name = _extract_project_name(text)
    priority = "urgent" if "urgent" in lowered else "high" if "high priority" in lowered else "medium"
    due_at = None
    if "tomorrow" in lowered:
        due_at = datetime.now(UTC) + timedelta(days=1)
    read_verbs = ("show", "list", "what", "which", "check", "find", "get", "who", "where", "status", "summary")
    if any(verb in lowered for verb in read_verbs):
        if any(word in lowered for word in ["task", "tasks", "todo", "to do", "needs to"]):
            return ExtractedAction(action_type="query_records", query_target="tasks", target_text=text, confidence=0.86)
        if any(word in lowered for word in ["person", "people", "contact", "email address", "phone"]) or lowered.startswith("who"):
            return ExtractedAction(action_type="query_records", query_target="people", target_text=text, confidence=0.84)
        if any(word in lowered for word in ["email", "emails", "n8n", "integration", "failed"]):
            return ExtractedAction(action_type="query_records", query_target="emails", target_text=text, confidence=0.84)
        if any(word in lowered for word in ["meeting", "meetings"]):
            return ExtractedAction(action_type="query_records", query_target="meetings", target_text=text, confidence=0.82)
        if "reminder" in lowered or "reminders" in lowered:
            return ExtractedAction(action_type="query_records", query_target="reminders", target_text=text, confidence=0.82)
        if "everything" in lowered or "summary" in lowered:
            return ExtractedAction(action_type="query_records", query_target="summary", target_text=text, confidence=0.82)
    completion_words = ["complete", "completed", "done", "mark"]
    task_references = ["task", "that", "it"]
    if any(word in lowered for word in completion_words) and any(word in lowered for word in task_references):
        related_task_id = last_task.id if last_task is not None else None
        return ExtractedAction(
            action_type="complete_task",
            title=last_task.title if last_task is not None else None,
            related_task_id=related_task_id,
            target_text=text,
            confidence=0.82,
            missing_fields=[] if related_task_id or state.get("referenced_tasks") else ["task"],
        )
    if "send" in lowered and "email" in lowered:
        recipient_email = person_emails or ([last_person.email] if last_person and last_person.email else [])
        recipient_name = person_names or ([last_person.full_name] if last_person else [])
        related_task_id = last_task.id if last_task is not None else None
        missing_fields: list[str] = []
        if not recipient_email:
            missing_fields.append("recipient_email")
        if last_task is None and not text:
            missing_fields.append("email_body")
        subject = f"Task: {last_task.title[:90]}" if last_task is not None else "Follow-up"
        return ExtractedAction(
            action_type="send_email",
            title=subject,
            description=text,
            person_names=recipient_name,
            person_emails=[email for email in recipient_email if email],
            subject=subject,
            body=_email_body(last_person, last_task, text),
            related_task_id=related_task_id,
            project_name=project_name,
            confidence=0.86,
            missing_fields=missing_fields,
        )
    if extracted_people and not any(word in lowered for word in ["task", "needs to", "send", "remind"]):
        return ExtractedAction(
            action_type="upsert_person",
            title=person_names[0] if person_names else None,
            description=text,
            person_names=person_names,
            person_emails=person_emails,
            project_name=project_name,
            confidence=0.82,
        )
    if "remind" in lowered:
        return ExtractedAction(
            action_type="create_reminder",
            title=text[:120] or "WhatsApp reminder",
            description=text,
            due_at=due_at,
            priority=priority,  # type: ignore[arg-type]
            confidence=0.72,
            missing_fields=[] if due_at else ["trigger_time"],
        )
    if any(word in lowered for word in ["task", "finish", "follow up", "follow-up", "needs to"]):
        return ExtractedAction(
            action_type="create_task",
            title=_task_title(text),
            description=text,
            person_names=person_names,
            person_emails=person_emails,
            project_name=project_name,
            due_at=due_at,
            priority=priority,  # type: ignore[arg-type]
            confidence=0.68,
        )
    return ExtractedAction(action_type="no_action", description=text, confidence=0.8)


async def load_context(state: AssistantState) -> AssistantState:
    message = state["message"]
    db = state["db"]
    conversation, last_person, last_task, last_project = _conversation_context(db, message)
    state["conversation"] = conversation
    state["last_person"] = last_person
    state["last_task"] = last_task
    text = message.text or ""
    people = _find_referenced_people(db, text, last_person)
    tasks = _find_referenced_tasks(db, text, people, last_task)
    project_name = _extract_project_name(text)
    project = None
    if project_name and people:
        project = db.scalar(select(Project).where(Project.person_id == people[0].id, Project.name.ilike(project_name)))
    if project is None:
        project = last_project
    if project is not None and not tasks:
        tasks = list(
            db.scalars(
                select(Task)
                .where(Task.project_id == project.id, Task.status.not_in(["completed", "cancelled"]))
                .order_by(Task.created_at.desc())
                .limit(10)
            )
        )
    if not people and last_person is not None:
        people = [last_person]
    state["referenced_people"] = people
    state["referenced_tasks"] = tasks
    state["referenced_project"] = project
    state["recent_messages"] = (
        list(db.scalars(select(Message).where(Message.conversation_id == conversation.id).order_by(Message.created_at.desc()).limit(12)))
        if conversation is not None
        else []
    )
    state["tool_errors"] = []
    state["read_result"] = None
    return state


async def classify_intent(state: AssistantState) -> AssistantState:
    settings = state["settings"]
    fallback_action = _heuristic_action(state)
    intent = "general_conversation"
    if fallback_action.action_type == "create_reminder":
        intent = "create_reminder"
    elif fallback_action.action_type == "create_task":
        intent = "create_task"
    elif fallback_action.action_type == "complete_task":
        intent = "complete_task"
    elif fallback_action.action_type == "send_email":
        intent = "send_email"
    elif fallback_action.action_type == "query_records":
        intent = "query_records"
    elif fallback_action.action_type == "upsert_person":
        intent = "record_person_note"
    fallback_intent = {
        "intent": intent,
        "confidence": fallback_action.confidence,
        "requires_confirmation": bool(fallback_action.missing_fields),
        "rationale": "Heuristic extraction from WhatsApp text.",
    }
    system = Path(__file__).parents[1].joinpath("prompts/system.md").read_text()
    model = chat_model(settings)
    raw = await structured_json(
        model,
        system + "\nReturn only JSON matching: intent, confidence, requires_confirmation, rationale.",
        _prompt_with_context(state),
        fallback_intent,
    )
    try:
        state["classification"] = ClassificationResult.model_validate(raw)
    except ValidationError:
        raw["intent"] = fallback_intent["intent"]
        raw.setdefault("confidence", fallback_intent["confidence"])
        raw.setdefault("requires_confirmation", fallback_intent["requires_confirmation"])
        raw["rationale"] = f"Model returned unsupported intent; {fallback_intent['rationale']}"
        state["classification"] = ClassificationResult.model_validate(raw)
    state["actions"] = [fallback_action]
    return state


def _prompt_with_context(state: AssistantState) -> str:
    message = state["message"]
    last_person = state.get("last_person")
    last_task = state.get("last_task")
    lines = ["Latest WhatsApp message:", message.text or ""]
    if last_person is not None:
        lines.append(f"Last related person: {last_person.full_name} <{last_person.email or 'no email'}>")
    if last_task is not None:
        project = state.get("referenced_project") or (state["db"].get(Project, last_task.project_id) if last_task.project_id else None)
        project_name = f" / project {project.name}" if project else ""
        lines.append(f"Last related task: {last_task.title}{project_name}")
    lines.append("Resolve pronouns like him/her/that from the last related person/task when unambiguous.")
    return "\n".join(lines)


def _resolve_people(db: Session, action: ExtractedAction, state: AssistantState) -> list[Person]:
    people: list[Person] = []
    for name, email in zip(action.person_names or [], action.person_emails or [], strict=False):
        people.append(_upsert_person(db, PersonCreate(full_name=name or _name_from_email(email), email=email)))
    if not people and state.get("last_person") is not None:
        people.append(state["last_person"])
    return people


def _remember(
    conversation: Conversation | None,
    *,
    person: Person | None = None,
    task: Task | None = None,
    project: Project | None = None,
    email_id: str | None = None,
) -> None:
    if conversation is None:
        return
    state = dict(conversation.state or {})
    if person is not None:
        state["last_person_id"] = person.id
    if task is not None:
        state["last_task_id"] = task.id
    if project is not None:
        state["last_project_id"] = project.id
    if email_id is not None:
        state["last_email_id"] = email_id
    conversation.state = state


def _first_action(state: AssistantState) -> ExtractedAction:
    return state.get("actions", [ExtractedAction(action_type="no_action")])[0]


def _format_task(db: Session, task: Task) -> str:
    person = db.get(Person, task.assigned_person_id) if task.assigned_person_id else None
    project = db.get(Project, task.project_id) if task.project_id else None
    assignee = f" for {person.full_name}" if person else ""
    project_label = f" [{project.name}]" if project else ""
    due = f", due {task.due_date.date().isoformat()}" if task.due_date else ""
    return f"{project_label} {task.title}{assignee} ({task.priority}, {task.status}{due})".strip()


async def records_agent(state: AssistantState) -> AssistantState:
    action = _first_action(state)
    if action.action_type != "query_records":
        return state
    db = state["db"]
    target = action.query_target or "summary"
    people = state.get("referenced_people", [])
    if target == "tasks":
        tasks = state.get("referenced_tasks", [])
        if not tasks:
            stmt = select(Task).where(Task.status.not_in(["completed", "cancelled"])).order_by(Task.created_at.desc()).limit(10)
            tasks = list(db.scalars(stmt))
        if not tasks:
            state["read_result"] = "I do not see any open tasks."
        else:
            state["read_result"] = "Open tasks:\n" + "\n".join(f"- {_format_task(db, task)}" for task in tasks[:10])
    elif target == "people":
        rows = people or list(db.scalars(select(Person).where(Person.active.is_(True)).order_by(Person.full_name).limit(10)))
        if not rows:
            state["read_result"] = "I do not see any saved people yet."
        else:
            state["read_result"] = "People I found:\n" + "\n".join(
                f"- {person.full_name} <{person.email or 'no email'}>" for person in rows[:10]
            )
    elif target == "emails":
        emails = list(db.scalars(select(Email).order_by(Email.created_at.desc()).limit(10)))
        if not emails:
            state["read_result"] = "I do not see any email records yet."
        else:
            lines = []
            for email in emails:
                recipients = db.scalars(select(EmailRecipient).where(EmailRecipient.email_id == email.id)).all()
                to = ", ".join(recipient.email_address for recipient in recipients) or "no recipient"
                lines.append(f"- {email.subject} to {to}: {email.status}")
            state["read_result"] = "Recent emails:\n" + "\n".join(lines)
    elif target == "meetings":
        meetings = list(db.scalars(select(Meeting).order_by(Meeting.start_time.asc()).limit(10)))
        state["read_result"] = (
            "Upcoming meetings:\n"
            + "\n".join(f"- {meeting.title} at {meeting.start_time.isoformat()} ({meeting.status})" for meeting in meetings)
            if meetings
            else "I do not see any meetings yet."
        )
    elif target == "reminders":
        reminders = list(db.scalars(select(Reminder).order_by(Reminder.trigger_time.asc()).limit(10)))
        state["read_result"] = (
            "Reminders:\n"
            + "\n".join(f"- {reminder.title} at {reminder.trigger_time.isoformat()} ({reminder.status})" for reminder in reminders)
            if reminders
            else "I do not see any reminders yet."
        )
    else:
        people_count = db.scalar(select(func.count()).select_from(Person).where(Person.active.is_(True))) or 0
        task_count = db.scalar(select(func.count()).select_from(Task).where(Task.status.not_in(["completed", "cancelled"]))) or 0
        failed_email_count = db.scalar(select(func.count()).select_from(Email).where(Email.status == "failed")) or 0
        state["read_result"] = f"I see {people_count} people, {task_count} open tasks, and {failed_email_count} failed email integrations."
    return state


async def people_agent(state: AssistantState) -> AssistantState:
    action = _first_action(state)
    if action.action_type not in {"upsert_person", "create_task", "send_email"}:
        return state
    db = state["db"]
    conversation = state.get("conversation")
    people = list(state.get("referenced_people", []))
    seen = {person.id for person in people}
    for name, email in zip(action.person_names or [], action.person_emails or [], strict=False):
        person = _upsert_person(db, PersonCreate(full_name=name or _name_from_email(email), email=email))
        if person.id not in seen:
            people.append(person)
            seen.add(person.id)
    if people:
        state["referenced_people"] = people
        state["last_person"] = people[0]
        _remember(conversation, person=people[0])
    if action.project_name and people:
        project = _upsert_project(db, people[0], action.project_name)
        state["referenced_project"] = project
        _remember(conversation, project=project)
    if action.action_type == "upsert_person" and people:
        state["persisted_entity_ids"] = state.get("persisted_entity_ids", []) + [people[0].id]
    return state


async def task_agent(state: AssistantState) -> AssistantState:
    action = _first_action(state)
    if action.action_type not in {"create_task", "complete_task"}:
        return state
    db = state["db"]
    message = state["message"]
    conversation = state.get("conversation")
    context = ToolContext(db=db, actor_type="openwa", actor_id=message.sender_phone, request_id=message.external_message_id)
    persisted = list(state.get("persisted_entity_ids", []))
    if action.action_type == "create_task" and action.title:
        people = state.get("referenced_people", [])
        project = state.get("referenced_project")
        if project is None and action.project_name and people:
            project = _upsert_project(db, people[0], action.project_name)
        result = create_task_tool(
            context,
            TaskCreate(
                title=action.title,
                description=action.description,
                priority=action.priority or "medium",
                assigned_person_id=people[0].id if people else None,
                project_id=project.id if project else None,
                due_date=action.due_at,
            ),
        )
        persisted.append(result["id"])
        task = db.get(Task, result["id"])
        current_message = db.scalar(select(Message).where(Message.external_message_id == message.external_message_id))
        if task is not None and current_message is not None:
            task.source_message_id = current_message.id
        state["last_task"] = task
        state["referenced_tasks"] = [task] if task is not None else []
        state["referenced_project"] = project
        _remember(conversation, person=people[0] if people else None, task=task, project=project)
    elif action.action_type == "complete_task":
        task = db.get(Task, action.related_task_id) if action.related_task_id else None
        if task is None and state.get("referenced_tasks"):
            task = state["referenced_tasks"][0]
        if task is None:
            state.setdefault("tool_errors", []).append("missing_task")
        else:
            task.status = "completed"
            task.completed_at = datetime.now(UTC)
            persisted.append(task.id)
            state["last_task"] = task
            _remember(conversation, task=task)
    state["persisted_entity_ids"] = persisted
    return state


async def reminder_agent(state: AssistantState) -> AssistantState:
    action = _first_action(state)
    if action.action_type != "create_reminder" or not action.title or not action.due_at:
        return state
    context = ToolContext(
        db=state["db"],
        actor_type="openwa",
        actor_id=state["message"].sender_phone,
        request_id=state["message"].external_message_id,
    )
    result = create_reminder_tool(
        context,
        ReminderCreate(title=action.title, description=action.description, trigger_time=action.due_at),
    )
    state["persisted_entity_ids"] = state.get("persisted_entity_ids", []) + [result["id"]]
    return state


async def email_agent(state: AssistantState) -> AssistantState:
    action = _first_action(state)
    if action.action_type != "send_email":
        return state
    db = state["db"]
    message = state["message"]
    people = state.get("referenced_people", [])
    related_task = db.get(Task, action.related_task_id) if action.related_task_id else None
    if related_task is None and state.get("referenced_tasks"):
        related_task = state["referenced_tasks"][0]
    if not people or not action.subject or not action.body:
        state.setdefault("tool_errors", []).append("missing_email_context")
        return state
    try:
        result = await send_email_tool(
            ToolContext(db=db, actor_type="openwa", actor_id=message.sender_phone, request_id=message.external_message_id),
            state["settings"],
            to_people=people,
            subject=action.subject,
            text_body=action.body,
            related_task=related_task,
        )
    except Exception:
        logger.exception("agent_send_email_failed", extra={"message_id": message.external_message_id})
        state.setdefault("tool_errors", []).append("email_send_failed")
        return state
    if result.get("ok"):
        state["persisted_entity_ids"] = state.get("persisted_entity_ids", []) + [str(result["id"])]
        project = db.get(Project, related_task.project_id) if related_task and related_task.project_id else state.get("referenced_project")
        _remember(state.get("conversation"), person=people[0], task=related_task, project=project, email_id=str(result["id"]))
        state["last_person"] = people[0]
        if related_task is not None:
            state["last_task"] = related_task
    else:
        state.setdefault("tool_errors", []).append("email_send_failed")
    return state


def _fallback_general_reply(text: str) -> str:
    lowered = text.strip().lower()
    greetings = {"hi", "hello", "hey", "مرحبا", "هلا", "اهلا", "أهلا"}
    if lowered in greetings:
        return "Hi! I am here. Tell me what you want me to handle: a task, reminder, meeting, email, or a quick note."
    if "noted" in lowered or "note" in lowered:
        return (
            "You are right. I was using a fallback reply. Tell me what you want done, "
            "and I will either handle it or ask one clear question."
        )
    return "I am here. Tell me what you want me to handle next."


async def _general_conversation_reply(state: AssistantState) -> str:
    message = state["message"]
    model = chat_model(state["settings"])
    if model is None:
        return _fallback_general_reply(message.text or "")
    system = Path(__file__).parents[1].joinpath("prompts/system.md").read_text()
    prompt = (
        f"{system}\n\n"
        "Reply to the latest WhatsApp message as Meet Tina.\n"
        "Keep it concise and useful, 1-4 short sentences.\n"
        "If the user only greets you, greet them and offer concrete things you can do.\n"
        "If they ask why something happened, answer directly.\n"
        "Do not say an action was saved unless the tool layer already saved it.\n"
        "Do not use the word 'Noted' as the whole reply."
    )
    try:
        response = await model.ainvoke([SystemMessage(content=prompt), HumanMessage(content=message.text or "")])
    except Exception:
        logger.exception("general_reply_generation_failed", extra={"message_id": message.external_message_id})
        return _fallback_general_reply(message.text or "")
    reply = str(response.content).strip()
    return reply or _fallback_general_reply(message.text or "")


async def generate_reply(state: AssistantState) -> AssistantState:
    action = state.get("actions", [ExtractedAction(action_type="no_action")])[0]
    if state.get("read_result"):
        state["reply"] = state["read_result"] or "I checked, but I did not find anything relevant."
    elif state.get("tool_errors"):
        if "email_send_failed" in state["tool_errors"]:
            state["reply"] = "I found the person and task, but the email send failed on the integration side. I logged it so you can retry."
        elif "missing_task" in state["tool_errors"]:
            state["reply"] = "I can update a task, but I could not tell which task you meant."
        else:
            state["reply"] = "I can send it, but I need the recipient email or the task content first."
    elif action.missing_fields:
        if "recipient_email" in action.missing_fields:
            state["reply"] = "Who should I send it to? I need the email address."
        else:
            state["reply"] = "I can help with that. What date and time should I use?"
    elif action.action_type == "create_task" and state.get("persisted_entity_ids"):
        person = state.get("last_person")
        state["reply"] = f"Done. I saved that as a task for {person.full_name}." if person else "Done. I saved that as a task."
    elif action.action_type == "complete_task" and state.get("persisted_entity_ids"):
        task = state.get("last_task")
        state["reply"] = f"Done. I marked {task.title} as completed." if task else "Done. I marked the task as completed."
    elif action.action_type == "upsert_person" and state.get("persisted_entity_ids"):
        person = state.get("last_person")
        state["reply"] = f"Done. I saved {person.full_name}." if person else "Done. I saved the contact."
    elif action.action_type == "send_email" and state.get("persisted_entity_ids"):
        person = state.get("last_person")
        target = f" to {person.full_name}" if person else ""
        state["reply"] = f"Done. I sent the email{target} with the task."
    elif action.action_type == "create_reminder" and state.get("persisted_entity_ids"):
        state["reply"] = "Done. I created the reminder."
    else:
        state["reply"] = await _general_conversation_reply(state)
    return state


def build_graph():
    graph = StateGraph(AssistantState)
    graph.add_node("load_context", load_context)
    graph.add_node("classify_intent", classify_intent)
    graph.add_node("records_agent", records_agent)
    graph.add_node("people_agent", people_agent)
    graph.add_node("task_agent", task_agent)
    graph.add_node("reminder_agent", reminder_agent)
    graph.add_node("email_agent", email_agent)
    graph.add_node("generate_reply", generate_reply)
    graph.set_entry_point("load_context")
    graph.add_edge("load_context", "classify_intent")
    graph.add_edge("classify_intent", "records_agent")
    graph.add_edge("records_agent", "people_agent")
    graph.add_edge("people_agent", "task_agent")
    graph.add_edge("task_agent", "reminder_agent")
    graph.add_edge("reminder_agent", "email_agent")
    graph.add_edge("email_agent", "generate_reply")
    graph.add_edge("generate_reply", END)
    return graph.compile()


async def run_assistant(settings: Settings, db: Session, message: NormalizedMessage) -> AgentResult:
    graph = build_graph()
    final_state: dict[str, Any] = await graph.ainvoke({"settings": settings, "db": db, "message": message})
    return AgentResult(
        reply=final_state["reply"],
        classification=final_state["classification"],
        actions=final_state.get("actions", []),
        persisted_entity_ids=final_state.get("persisted_entity_ids", []),
        requires_confirmation=final_state["classification"].requires_confirmation,
    )
