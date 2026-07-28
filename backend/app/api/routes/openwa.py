import asyncio
import base64
import binascii
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agent.graphs.personal_assistant import run_assistant
from app.core.config import Settings, get_settings
from app.core.database import get_db
from app.integrations.ai.client import OpenAICompatibleTranscriptionProvider
from app.integrations.openwa.client import OpenWAClient
from app.integrations.openwa.normalizer import normalize_openwa_event
from app.models.entities import Conversation, File, Message
from app.schemas.integrations import OpenWAWebhookResponse
from app.services.audit import write_audit
from app.services.replay import claim_replay_key
from app.storage.audio import transcode_audio_to_mp3
from app.storage.local import LocalStorage, normalize_mime_type

router = APIRouter()
logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class EmbeddedMedia:
    content: bytes
    mime_type: str
    file_name: str | None = None


def _payload_candidates(event: dict[str, Any]) -> list[dict[str, Any]]:
    candidates = [event]
    for key in ("message", "data", "payload", "body"):
        value = event.get(key)
        if isinstance(value, dict):
            candidates.append(value)
    return candidates


def _extract_embedded_media(event: dict[str, Any]) -> EmbeddedMedia | None:
    for payload in _payload_candidates(event):
        metadata = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
        for media in (metadata.get("media"), payload.get("media"), payload.get("mediaData")):
            if not isinstance(media, dict):
                continue
            encoded = media.get("data") or media.get("base64") or media.get("body")
            if not isinstance(encoded, str) or not encoded:
                continue
            mime_type = media.get("mimetype") or media.get("mimeType") or payload.get("mimetype") or payload.get("mimeType")
            if not isinstance(mime_type, str) or not mime_type:
                mime_type = "application/octet-stream"
            try:
                content = base64.b64decode(encoded, validate=True)
            except binascii.Error:
                content = base64.b64decode(encoded)
            file_name = media.get("filename") or payload.get("filename")
            return EmbeddedMedia(content=content, mime_type=normalize_mime_type(mime_type), file_name=str(file_name) if file_name else None)
    return None


def _file_path(settings: Settings, file: File) -> Path:
    return (settings.data_dir / file.relative_path).resolve()


def _store_embedded_media(db: Session, settings: Settings, event: dict[str, Any], message_id: str) -> File | None:
    embedded = _extract_embedded_media(event)
    if embedded is None:
        return None
    file = LocalStorage(settings).save_bytes(
        db,
        content=embedded.content,
        mime_type=embedded.mime_type,
        source="openwa",
        original_file_name=embedded.file_name,
    )
    file.related_message_id = message_id
    return file


async def _mp3_copy_for_transcription(db: Session, settings: Settings, source_file: File, message_id: str) -> File:
    if not source_file.mime_type.startswith("audio/"):
        return source_file
    source_path = _file_path(settings, source_file)
    mp3_path = await asyncio.to_thread(transcode_audio_to_mp3, source_path)
    if mp3_path is None or mp3_path == source_path:
        return source_file
    mp3_file = LocalStorage(settings).save_bytes(
        db,
        content=mp3_path.read_bytes(),
        mime_type="audio/mpeg",
        source="openwa_transcoded",
        original_file_name=f"{Path(source_file.original_file_name or source_file.safe_file_name).stem}.mp3",
    )
    mp3_file.related_message_id = message_id
    return mp3_file


async def _transcribe_voice(settings: Settings, db: Session, media_file: File | None, message_id: str) -> tuple[str | None, File | None]:
    if media_file is None or not media_file.mime_type.startswith("audio/") or not settings.ai_transcription_model:
        return None, media_file
    transcription_file = await _mp3_copy_for_transcription(db, settings, media_file, message_id)
    transcript = await OpenAICompatibleTranscriptionProvider(settings).transcribe(_file_path(settings, transcription_file))
    text = transcript.text.strip()
    return (text or None), transcription_file


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
    token: str | None = Query(default=None),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> OpenWAWebhookResponse:
    if request.headers.get("content-type", "").split(";")[0] != "application/json":
        raise HTTPException(status_code=415, detail="Expected application/json")
    event = await request.json()
    _verify_openwa(settings, x_openwa_token or token, event)
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
    db.flush()
    media_file = _store_embedded_media(db, settings, event, message.id)
    if media_file is not None:
        message.media_id = media_file.id
    if normalized.message_type == "voice" and not normalized.text:
        try:
            transcript, transcription_file = await _transcribe_voice(settings, db, media_file, message.id)
        except Exception:
            logger.exception("openwa_voice_transcription_failed", extra={"message_id": normalized.external_message_id})
            message.processing_status = "transcription_failed"
            reply = "I received the voice note, but I could not transcribe it yet. Please resend it as text or try another voice note."
            outbound = Message(
                external_message_id=f"assistant:{normalized.external_message_id}",
                conversation_id=conversation.id,
                direction="outbound",
                message_type="text",
                text=reply,
                raw_event={"error": "voice_transcription_failed"},
            )
            db.add(outbound)
            await OpenWAClient(settings).send_text(normalized.conversation_id, reply)
            db.commit()
            return OpenWAWebhookResponse(status="processed", message_id=normalized.external_message_id, reply=reply)
        if transcription_file is not None:
            message.media_id = transcription_file.id
        if transcript:
            media_path = transcription_file.relative_path if transcription_file else None
            normalized = normalized.model_copy(update={"text": transcript, "media_path": media_path})
            message.text = transcript
            message.processing_status = "transcribed"
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
