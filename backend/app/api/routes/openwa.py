from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agent.graphs.personal_assistant import run_assistant
from app.core.config import Settings, get_settings
from app.core.database import get_db
from app.integrations.openwa.client import OpenWAClient
from app.integrations.openwa.normalizer import normalize_openwa_event
from app.models.entities import Conversation, Message
from app.schemas.integrations import OpenWAWebhookResponse
from app.services.audit import write_audit
from app.services.replay import claim_replay_key

router = APIRouter()


def _verify_openwa(settings: Settings, token: str | None, event: dict) -> None:
    if settings.openwa_webhook_secret and token != settings.openwa_webhook_secret:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid OpenWA token")
    instance_id = event.get("instanceId") or event.get("sessionId")
    if settings.openwa_allowed_instance_id and instance_id != settings.openwa_allowed_instance_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Unexpected OpenWA instance")
    if settings.openwa_session_id and event.get("sessionId") and event.get("sessionId") != settings.openwa_session_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Unexpected OpenWA session")


@router.post("/openwa", response_model=OpenWAWebhookResponse)
async def openwa_webhook(
    request: Request,
    x_openwa_token: str | None = Header(default=None, alias="X-OpenWA-Token"),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> OpenWAWebhookResponse:
    if request.headers.get("content-type", "").split(";")[0] != "application/json":
        raise HTTPException(status_code=415, detail="Expected application/json")
    event = await request.json()
    _verify_openwa(settings, x_openwa_token, event)
    normalized = normalize_openwa_event(event)
    if not normalized.external_message_id or not normalized.conversation_id:
        raise HTTPException(status_code=422, detail="Missing OpenWA message or conversation id")
    claimed = claim_replay_key(db, source="openwa", key=normalized.external_message_id, ttl_seconds=settings.openwa_replay_window_seconds)
    if not claimed:
        return OpenWAWebhookResponse(status="duplicate", message_id=normalized.external_message_id)

    conversation = db.scalar(select(Conversation).where(Conversation.whatsapp_chat_id == normalized.conversation_id))
    if conversation is None:
        conversation = Conversation(whatsapp_chat_id=normalized.conversation_id, contact_phone=normalized.sender_phone)
        db.add(conversation)
        db.flush()
    message = Message(
        external_message_id=normalized.external_message_id,
        conversation_id=conversation.id,
        direction="inbound",
        message_type=normalized.message_type,
        text=normalized.text,
        raw_event=normalized.raw_event,
    )
    db.add(message)
    write_audit(
        db,
        actor_type="openwa",
        actor_id=normalized.sender_phone,
        action="receive_message",
        entity_type="message",
        entity_id=message.id,
    )
    result = await run_assistant(settings, db, normalized)
    message.processing_status = "processed"
    outbound = Message(
        external_message_id=f"assistant:{normalized.external_message_id}",
        conversation_id=conversation.id,
        direction="outbound",
        message_type="text",
        text=result.reply,
        raw_event={"classification": result.classification.model_dump(), "actions": [a.model_dump(mode="json") for a in result.actions]},
    )
    db.add(outbound)
    await OpenWAClient(settings).send_text(normalized.conversation_id, result.reply)
    db.commit()
    return OpenWAWebhookResponse(status="processed", message_id=normalized.external_message_id, reply=result.reply)
