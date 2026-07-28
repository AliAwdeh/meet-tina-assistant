from datetime import UTC, datetime
from typing import Any

from app.schemas.domain import NormalizedMessage


def _message_type(event: dict[str, Any]) -> str:
    explicit = str(event.get("type") or event.get("mimetype") or "").lower()
    if "audio" in explicit or event.get("isVoice"):
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
    message_id = str(event.get("id") or event.get("messageId") or event.get("external_message_id") or "")
    chat_id = str(event.get("chatId") or event.get("from") or event.get("conversation_id") or "")
    sender_phone = str(event.get("sender", {}).get("id") if isinstance(event.get("sender"), dict) else event.get("from") or "")
    sender_name = event.get("sender", {}).get("pushname") if isinstance(event.get("sender"), dict) else event.get("senderName")
    timestamp_raw = event.get("timestamp") or event.get("t")
    if isinstance(timestamp_raw, int | float):
        timestamp = datetime.fromtimestamp(timestamp_raw / 1000 if timestamp_raw > 10_000_000_000 else timestamp_raw, UTC)
    else:
        timestamp = datetime.now(UTC)
    text = event.get("body") or event.get("text") or event.get("caption")
    media_path = event.get("mediaUrl") or event.get("clientUrl") or event.get("deprecatedMms3Url")
    mime_type = event.get("mimetype") or event.get("mimeType")
    return NormalizedMessage(
        external_message_id=message_id[:255],
        conversation_id=chat_id[:255],
        sender_phone=sender_phone,
        sender_name=str(sender_name)[:255] if sender_name else None,
        message_type=_message_type(event),  # type: ignore[arg-type]
        text=str(text)[:10_000] if text else None,
        media_path=str(media_path) if media_path else None,
        mime_type=str(mime_type)[:255] if mime_type else None,
        timestamp=timestamp,
        raw_event=event,
    )
