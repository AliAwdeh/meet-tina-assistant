import logging
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, TypedDict

from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.graph import END, StateGraph
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agent.tools.registry import ToolContext, create_reminder_tool, create_task_tool, send_email_tool
from app.core.config import Settings
from app.integrations.ai.client import chat_model, structured_json
from app.models.entities import Conversation, Message, Person, Task
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


def _conversation_context(db: Session, message: NormalizedMessage) -> tuple[Conversation | None, Person | None, Task | None]:
    conversation = db.scalar(select(Conversation).where(Conversation.whatsapp_chat_id == message.conversation_id))
    state = conversation.state if conversation and conversation.state else {}
    last_person = db.get(Person, state.get("last_person_id")) if state.get("last_person_id") else None
    last_task = db.get(Task, state.get("last_task_id")) if state.get("last_task_id") else None
    if last_task is None and conversation is not None:
        message_ids = select(Message.id).where(Message.conversation_id == conversation.id)
        last_task = db.scalar(select(Task).where(Task.source_message_id.in_(message_ids)).order_by(Task.created_at.desc()))
    return conversation, last_person, last_task


def _task_title(text: str) -> str:
    match = re.search(r"\bneeds?\s+to\s+(?P<title>.+)", text, flags=re.IGNORECASE)
    if match:
        return match.group("title").strip()[:255]
    return text.strip()[:255] or "WhatsApp task"


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
    priority = "high" if "high priority" in lowered or "urgent" in lowered else "medium"
    due_at = None
    if "tomorrow" in lowered:
        due_at = datetime.now(UTC) + timedelta(days=1)
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
            confidence=0.86,
            missing_fields=missing_fields,
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
            due_at=due_at,
            priority=priority,  # type: ignore[arg-type]
            confidence=0.68,
        )
    return ExtractedAction(action_type="no_action", description=text, confidence=0.8)


async def classify_intent(state: AssistantState) -> AssistantState:
    message = state["message"]
    settings = state["settings"]
    db = state["db"]
    conversation, last_person, last_task = _conversation_context(db, message)
    state["conversation"] = conversation
    state["last_person"] = last_person
    state["last_task"] = last_task
    state["tool_errors"] = []
    fallback_action = _heuristic_action(state)
    intent = "general_conversation"
    if fallback_action.action_type == "create_reminder":
        intent = "create_reminder"
    elif fallback_action.action_type == "create_task":
        intent = "create_task"
    elif fallback_action.action_type == "send_email":
        intent = "send_email"
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
        lines.append(f"Last related task: {last_task.title}")
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
    email_id: str | None = None,
) -> None:
    if conversation is None:
        return
    state = dict(conversation.state or {})
    if person is not None:
        state["last_person_id"] = person.id
    if task is not None:
        state["last_task_id"] = task.id
    if email_id is not None:
        state["last_email_id"] = email_id
    conversation.state = state


async def execute_tools(state: AssistantState) -> AssistantState:
    db = state["db"]
    message = state["message"]
    settings = state["settings"]
    conversation = state.get("conversation")
    persisted: list[str] = []
    context = ToolContext(db=db, actor_type="openwa", actor_id=message.sender_phone, request_id=message.external_message_id)
    for action in state.get("actions", []):
        if action.missing_fields or action.confidence < 0.55:
            continue
        if action.action_type == "create_task" and action.title:
            people = _resolve_people(db, action, state)
            result = create_task_tool(
                context,
                TaskCreate(
                    title=action.title,
                    description=action.description,
                    priority=action.priority or "medium",
                    assigned_person_id=people[0].id if people else None,
                    due_date=action.due_at,
                ),
            )
            persisted.append(result["id"])
            task = db.get(Task, result["id"])
            current_message = db.scalar(select(Message).where(Message.external_message_id == message.external_message_id))
            if task is not None and current_message is not None:
                task.source_message_id = current_message.id
            _remember(conversation, person=people[0] if people else None, task=task)
            if people:
                state["last_person"] = people[0]
            if task is not None:
                state["last_task"] = task
        elif action.action_type == "create_reminder" and action.title and action.due_at:
            result = create_reminder_tool(
                context,
                ReminderCreate(title=action.title, description=action.description, trigger_time=action.due_at),
            )
            persisted.append(result["id"])
        elif action.action_type == "send_email":
            people = _resolve_people(db, action, state)
            related_task = db.get(Task, action.related_task_id) if action.related_task_id else state.get("last_task")
            if not people or not action.subject or not action.body:
                state.setdefault("tool_errors", []).append("missing_email_context")
                continue
            try:
                result = await send_email_tool(
                    context,
                    settings,
                    to_people=people,
                    subject=action.subject,
                    text_body=action.body,
                    related_task=related_task,
                )
            except Exception:
                logger.exception("agent_send_email_failed", extra={"message_id": message.external_message_id})
                state.setdefault("tool_errors", []).append("email_send_failed")
                continue
            if result.get("ok"):
                persisted.append(str(result["id"]))
                _remember(conversation, person=people[0], task=related_task, email_id=str(result["id"]))
                state["last_person"] = people[0]
                if related_task is not None:
                    state["last_task"] = related_task
    state["persisted_entity_ids"] = persisted
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
    if state.get("tool_errors"):
        if "email_send_failed" in state["tool_errors"]:
            state["reply"] = "I found the person and task, but the email send failed on the integration side. I logged it so you can retry."
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
    graph.add_node("classify_intent", classify_intent)
    graph.add_node("execute_tools", execute_tools)
    graph.add_node("generate_reply", generate_reply)
    graph.set_entry_point("classify_intent")
    graph.add_edge("classify_intent", "execute_tools")
    graph.add_edge("execute_tools", "generate_reply")
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
