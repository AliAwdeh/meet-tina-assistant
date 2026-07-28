import base64

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.core.database import SessionLocal
from app.models.entities import Email, EmailRecipient, File, Message, N8nRequest, Person, Task


def _event(message_id: str = "msg-1") -> dict[str, object]:
    return {
        "id": message_id,
        "chatId": "96170000000@c.us",
        "from": "+96170000000",
        "body": "Remind me tomorrow to call Sami",
        "type": "chat",
        "sessionId": "session",
    }


def test_openwa_accepts_general_greeting(client: TestClient) -> None:
    event = _event("msg-greeting")
    event["body"] = "Hi"
    response = client.post("/webhooks/openwa", json=event, headers={"X-OpenWA-Token": "test-openwa"})
    assert response.status_code == 200
    assert response.json()["status"] == "processed"
    assert response.json()["reply"] != "Noted."
    assert "task" in response.json()["reply"].lower()


def test_openwa_rejects_bad_token(client: TestClient) -> None:
    response = client.post("/webhooks/openwa", json=_event(), headers={"X-OpenWA-Token": "wrong"})
    assert response.status_code == 401


def test_openwa_accepts_query_token(client: TestClient) -> None:
    response = client.post("/webhooks/openwa?token=test-openwa", json=_event("msg-query-token"))
    assert response.status_code == 200
    assert response.json()["status"] == "processed"


def test_openwa_accepts_wrapped_message_payload(client: TestClient) -> None:
    event = {
        "event": "message.received",
        "sessionId": "session",
        "data": {
            "waMessageId": "wrapped-message",
            "chatId": "102907500351574@lid",
            "from": "102907500351574@lid",
            "to": "96171056438@c.us",
            "body": "Hi",
            "type": "text",
            "direction": "incoming",
            "timestamp": 1785252118,
            "chatName": "Ali Awdeh",
        },
    }
    response = client.post("/webhooks/openwa?token=test-openwa", json=event)
    assert response.status_code == 200
    assert response.json()["status"] == "processed"


def test_openwa_stores_wrapped_voice_media(client: TestClient) -> None:
    event = {
        "event": "message.received",
        "sessionId": "session",
        "data": {
            "waMessageId": "voice-message",
            "chatId": "102907500351574@lid",
            "from": "102907500351574@lid",
            "to": "96171056438@c.us",
            "body": "",
            "type": "voice",
            "direction": "incoming",
            "timestamp": 1785252118,
            "metadata": {
                "media": {
                    "mimetype": "audio/ogg; codecs=opus",
                    "data": base64.b64encode(b"OggSfake-audio").decode(),
                }
            },
        },
    }
    response = client.post("/webhooks/openwa?token=test-openwa", json=event)
    assert response.status_code == 200
    assert response.json()["status"] == "processed"
    with SessionLocal() as db:
        message = db.scalar(select(Message).where(Message.external_message_id == "voice-message"))
        assert message is not None
        assert message.message_type == "voice"
        assert message.media_id is not None
        media = db.get(File, message.media_id)
        assert media is not None
        assert media.mime_type == "audio/ogg"
        assert media.safe_file_name.endswith(".ogg")


def test_openwa_uses_context_to_assign_task_and_send_email(client: TestClient) -> None:
    first = {
        "event": "message.received",
        "sessionId": "session",
        "data": {
            "waMessageId": "context-task",
            "chatId": "102907500351574@lid",
            "from": "102907500351574@lid",
            "body": "Ali awdeh email ali.awdeh@maids.cc needs to email a report to me and the ceo mario",
            "type": "text",
        },
    }
    first_response = client.post("/webhooks/openwa?token=test-openwa", json=first)
    assert first_response.status_code == 200
    assert "task for Ali Awdeh" in first_response.json()["reply"]

    second = {
        "event": "message.received",
        "sessionId": "session",
        "data": {
            "waMessageId": "context-email",
            "chatId": "102907500351574@lid",
            "from": "102907500351574@lid",
            "body": "Send him that as an email",
            "type": "text",
        },
    }
    second_response = client.post("/webhooks/openwa?token=test-openwa", json=second)
    assert second_response.status_code == 200
    assert "sent the email to Ali Awdeh" in second_response.json()["reply"]

    with SessionLocal() as db:
        ali = db.scalar(select(Person).where(Person.email == "ali.awdeh@maids.cc"))
        assert ali is not None
        task = db.scalar(select(Task).where(Task.assigned_person_id == ali.id))
        assert task is not None
        assert task.title == "email a report to me and the ceo mario"
        email = db.scalar(select(Email))
        assert email is not None
        assert email.status == "queued"
        recipient = db.scalar(select(EmailRecipient).where(EmailRecipient.email_id == email.id))
        assert recipient is not None
        assert recipient.email_address == "ali.awdeh@maids.cc"
        n8n_request = db.scalar(select(N8nRequest).where(N8nRequest.request_id == email.n8n_request_id))
        assert n8n_request is not None
        assert n8n_request.response == {"skipped": "N8N_EMAIL_WEBHOOK_URL not configured"}


def test_openwa_can_read_and_update_related_records(client: TestClient) -> None:
    first = {
        "event": "message.received",
        "sessionId": "session",
        "data": {
            "waMessageId": "orchestration-task",
            "chatId": "102907500351574@lid",
            "from": "102907500351574@lid",
            "body": "Maya Haddad email maya@example.com needs to prepare the supplier follow up",
            "type": "text",
        },
    }
    assert client.post("/webhooks/openwa?token=test-openwa", json=first).status_code == 200

    query = {
        "event": "message.received",
        "sessionId": "session",
        "data": {
            "waMessageId": "orchestration-query",
            "chatId": "102907500351574@lid",
            "from": "102907500351574@lid",
            "body": "What tasks does Maya have?",
            "type": "text",
        },
    }
    query_response = client.post("/webhooks/openwa?token=test-openwa", json=query)
    assert query_response.status_code == 200
    assert "prepare the supplier follow up" in query_response.json()["reply"]
    assert "Maya Haddad" in query_response.json()["reply"]

    complete = {
        "event": "message.received",
        "sessionId": "session",
        "data": {
            "waMessageId": "orchestration-complete",
            "chatId": "102907500351574@lid",
            "from": "102907500351574@lid",
            "body": "Mark that task done",
            "type": "text",
        },
    }
    complete_response = client.post("/webhooks/openwa?token=test-openwa", json=complete)
    assert complete_response.status_code == 200
    assert "marked" in complete_response.json()["reply"].lower()

    with SessionLocal() as db:
        maya = db.scalar(select(Person).where(Person.email == "maya@example.com"))
        assert maya is not None
        task = db.scalar(select(Task).where(Task.assigned_person_id == maya.id))
        assert task is not None
        assert task.status == "completed"


def test_openwa_duplicate_event_is_idempotent(client: TestClient) -> None:
    headers = {"X-OpenWA-Token": "test-openwa"}
    first = client.post("/webhooks/openwa", json=_event(), headers=headers)
    second = client.post("/webhooks/openwa", json=_event(), headers=headers)
    assert first.status_code == 200
    assert first.json()["status"] == "processed"
    assert second.status_code == 200
    assert second.json()["status"] == "duplicate"
