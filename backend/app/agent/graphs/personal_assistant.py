from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, TypedDict

from langgraph.graph import END, StateGraph
from sqlalchemy.orm import Session

from app.agent.tools.registry import ToolContext, create_reminder_tool, create_task_tool
from app.core.config import Settings
from app.integrations.ai.client import chat_model, structured_json
from app.schemas.agent import AgentResult, ClassificationResult, ExtractedAction
from app.schemas.domain import NormalizedMessage, ReminderCreate, TaskCreate


class AssistantState(TypedDict, total=False):
    settings: Settings
    db: Session
    message: NormalizedMessage
    classification: ClassificationResult
    actions: list[ExtractedAction]
    persisted_entity_ids: list[str]
    reply: str


def _heuristic_action(message: NormalizedMessage) -> ExtractedAction:
    text = (message.text or "").strip()
    lowered = text.lower()
    priority = "high" if "high priority" in lowered or "urgent" in lowered else "medium"
    due_at = None
    if "tomorrow" in lowered:
        due_at = datetime.now(UTC) + timedelta(days=1)
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
            title=text[:120] or "WhatsApp task",
            description=text,
            due_at=due_at,
            priority=priority,  # type: ignore[arg-type]
            confidence=0.68,
        )
    return ExtractedAction(action_type="no_action", description=text, confidence=0.8)


async def classify_intent(state: AssistantState) -> AssistantState:
    message = state["message"]
    settings = state["settings"]
    fallback_action = _heuristic_action(message)
    intent = "general_conversation"
    if fallback_action.action_type == "create_reminder":
        intent = "create_reminder"
    elif fallback_action.action_type == "create_task":
        intent = "create_task"
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
        message.text or "",
        fallback_intent,
    )
    state["classification"] = ClassificationResult.model_validate(raw)
    state["actions"] = [fallback_action]
    return state


async def execute_tools(state: AssistantState) -> AssistantState:
    db = state["db"]
    message = state["message"]
    persisted: list[str] = []
    context = ToolContext(db=db, actor_type="openwa", actor_id=message.sender_phone, request_id=message.external_message_id)
    for action in state.get("actions", []):
        if action.missing_fields or action.confidence < 0.55:
            continue
        if action.action_type == "create_task" and action.title:
            result = create_task_tool(
                context,
                TaskCreate(
                    title=action.title,
                    description=action.description,
                    priority=action.priority or "medium",
                    due_date=action.due_at,
                ),
            )
            persisted.append(result["id"])
        elif action.action_type == "create_reminder" and action.title and action.due_at:
            result = create_reminder_tool(
                context,
                ReminderCreate(title=action.title, description=action.description, trigger_time=action.due_at),
            )
            persisted.append(result["id"])
    state["persisted_entity_ids"] = persisted
    return state


async def generate_reply(state: AssistantState) -> AssistantState:
    action = state.get("actions", [ExtractedAction(action_type="no_action")])[0]
    if action.missing_fields:
        state["reply"] = "I can help with that. What date and time should I use?"
    elif action.action_type == "create_task" and state.get("persisted_entity_ids"):
        state["reply"] = "Done. I saved that as a task."
    elif action.action_type == "create_reminder" and state.get("persisted_entity_ids"):
        state["reply"] = "Done. I created the reminder."
    else:
        state["reply"] = "Noted."
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
