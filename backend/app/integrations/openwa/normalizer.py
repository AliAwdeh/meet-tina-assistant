from datetime import UTC, datetime
from typing import Any

from app.schemas.domain import NormalizedMessage


def _message_payload(event: dict[str, Any]) -> dict[str, Any]:
    for key in ("message", "data", "payload", "body"):
        value = event.get(key)
        if isinstance(value, dict):
            return value
    return event


def _pick(source: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        value = source.get(key)
        if value not in (None, ""):
            return value
    return None


def _message_type(event: dict[str, Any]) -> str:
    metadata = event.get("metadata") if isinstance(event.get("metadata"), dict) else {}
    media = metadata.get("media") if isinstance(metadata.get("media"), dict) else {}
    explicit = str(
        event.get("type")
        or event.get("mimetype")
        or event.get("mimeType")
        or media.get("mimetype")
        or media.get("mimeType")
        or ""
    ).lower()
    if "audio" in explicit or "voice" in explicit or event.get("isVoice"):
        return "voice"
    if "image" in explicit:
        return "image"
    if event.get("lat") is not None and event.get("lng") is not None:
        return "location"
    if event.get("filename") or event.get("mediaData") or "pdf" in explicit:
        return "document"
    if event.get("body") or event.get("text"):
        return "text"
    return "other"


def normalize_openwa_event(event: dict[str, Any]) -> NormalizedMessage:
    payload = _message_payload(event)
    chat = payload.get("chat") if isinstance(payload.get("chat"), dict) else {}
    sender = payload.get("sender") if isinstance(payload.get("sender"), dict) else {}
    contact = payload.get("contact") if isinstance(payload.get("contact"), dict) else {}
    message_id = str(
        _pick(payload, "waMessageId", "messageId", "id", "external_message_id")
        or _pick(event, "waMessageId", "messageId", "id")
        or ""
    )
    chat_id = str(
        _pick(payload, "chatId", "conversation_id", "remoteJid", "from")
        or _pick(chat, "id", "chatId")
        or _pick(event, "chatId", "conversation_id", "remoteJid", "from")
        or ""
    )
    sender_phone = str(
        _pick(sender, "id", "phone", "number")
        or _pick(contact, "id", "phone", "number")
        or _pick(payload, "from", "senderId")
        or _pick(event, "from", "senderId")
        or ""
    )
    sender_name = (
        _pick(sender, "pushname", "name")
        or _pick(contact, "pushname", "name")
        or _pick(payload, "chatName", "senderName")
        or event.get("senderName")
    )
    timestamp_raw = _pick(payload, "timestamp", "t") or _pick(event, "timestamp", "t")
    if isinstance(timestamp_raw, int | float):
        timestamp = datetime.fromtimestamp(timestamp_raw / 1000 if timestamp_raw > 10_000_000_000 else timestamp_raw, UTC)
    else:
        timestamp = datetime.now(UTC)
    text = _pick(payload, "body", "text", "caption", "content")
    media_path = _pick(payload, "mediaUrl", "clientUrl", "deprecatedMms3Url")
    metadata = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
    media = metadata.get("media") if isinstance(metadata.get("media"), dict) else {}
    mime_type = _pick(payload, "mimetype", "mimeType") or _pick(media, "mimetype", "mimeType")
    return NormalizedMessage(
        external_message_id=message_id[:255],
        conversation_id=chat_id[:255],
        sender_phone=sender_phone,
        sender_name=str(sender_name)[:255] if sender_name else None,
        message_type=_message_type(payload),  # type: ignore[arg-type]
        text=str(text)[:10_000] if text else None,
        media_path=str(media_path) if media_path else None,
        mime_type=str(mime_type)[:255] if mime_type else None,
        timestamp=timestamp,
        raw_event=event,
    )
