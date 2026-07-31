import base64
from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.core.database import SessionLocal
from app.models.entities import Conversation, Email, EmailRecipient, File, Message, N8nRequest, Person, Project, Task


def _mock_planner(monkeypatch: Any, actions: list[dict[str, object]]) -> None:
    async def fake_structured_json(model: object, system: str, user: str, fallback: dict[str, object]) -> dict[str, object]:
        if "Return only JSON matching: intent" in system:
            return {
                "intent": actions[0].get("action_type", "update_task"),
                "confidence": 0.95,
                "requires_confirmation": False,
                "rationale": "test",
            }
        return {"actions": actions}

    monkeypatch.setattr("app.agent.graphs.personal_assistant.structured_json", fake_structured_json)


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


def test_openwa_lists_person_tasks_with_projects(client: TestClient) -> None:
    first = {
        "event": "message.received",
        "sessionId": "session",
        "data": {
            "waMessageId": "project-task",
            "chatId": "102907500351574@lid",
            "from": "102907500351574@lid",
            "body": "Sara Nassar email sara@example.com project Website Redesign needs to review homepage copy urgent",
            "type": "text",
        },
    }
    first_response = client.post("/webhooks/openwa?token=test-openwa", json=first)
    assert first_response.status_code == 200

    query = {
        "event": "message.received",
        "sessionId": "session",
        "data": {
            "waMessageId": "project-task-query",
            "chatId": "102907500351574@lid",
            "from": "102907500351574@lid",
            "body": "What tasks does Sara have?",
            "type": "text",
        },
    }
    query_response = client.post("/webhooks/openwa?token=test-openwa", json=query)
    assert query_response.status_code == 200
    reply = query_response.json()["reply"]
    assert "Website Redesign" in reply
    assert "review homepage copy" in reply
    assert "Urgent" in reply

    with SessionLocal() as db:
        sara = db.scalar(select(Person).where(Person.email == "sara@example.com"))
        assert sara is not None
        project = db.scalar(select(Project).where(Project.person_id == sara.id, Project.name == "Website Redesign"))
        assert project is not None
        task = db.scalar(select(Task).where(Task.assigned_person_id == sara.id))
        assert task is not None
        assert task.project_id == project.id
        assert task.priority == "urgent"


def test_openwa_new_person_task_does_not_reuse_old_project(client: TestClient, monkeypatch: Any) -> None:
    with SessionLocal() as db:
        ali = Person(full_name="Ali Awdeh", email="ali@example.com")
        db.add(ali)
        db.flush()
        old_project = Project(person_id=ali.id, name="Old Context Project")
        db.add(old_project)
        db.flush()
        db.add(
            Conversation(
                whatsapp_chat_id="102907500351574@lid",
                contact_phone="102907500351574",
                state={"last_person_id": ali.id, "last_project_id": old_project.id},
            )
        )
        db.commit()
        old_project_id = old_project.id

    _mock_planner(
        monkeypatch,
        [
            {
                "action_type": "create_task",
                "title": "Travel Assist",
                "person_names": ["Ali Awdeh"],
                "project_id": old_project_id,
                "confidence": 0.95,
            }
        ],
    )

    event = {
        "event": "message.received",
        "sessionId": "session",
        "data": {
            "waMessageId": "person-task-no-project-reuse",
            "chatId": "102907500351574@lid",
            "from": "102907500351574@lid",
            "body": "Create a new task for Ali called Travel Assist",
            "type": "text",
        },
    }
    response = client.post("/webhooks/openwa?token=test-openwa", json=event)
    assert response.status_code == 200
    assert "task for Ali Awdeh" in response.json()["reply"]
    assert "No project attached" in response.json()["reply"]
    assert "Available projects for Ali Awdeh: Old Context Project" in response.json()["reply"]

    with SessionLocal() as db:
        ali = db.scalar(select(Person).where(Person.email == "ali@example.com"))
        assert ali is not None
        task = db.scalar(select(Task).where(Task.assigned_person_id == ali.id, Task.title == "Travel Assist"))
        assert task is not None
        assert task.project_id is None


def test_openwa_can_move_task_between_projects_and_change_priority(client: TestClient) -> None:
    create = {
        "event": "message.received",
        "sessionId": "session",
        "data": {
            "waMessageId": "project-update-create",
            "chatId": "102907500351574@lid",
            "from": "102907500351574@lid",
            "body": "Lina Karam email lina@example.com project Client Portal needs to prepare the onboarding checklist",
            "type": "text",
        },
    }
    assert client.post("/webhooks/openwa?token=test-openwa", json=create).status_code == 200

    move = {
        "event": "message.received",
        "sessionId": "session",
        "data": {
            "waMessageId": "project-update-move",
            "chatId": "102907500351574@lid",
            "from": "102907500351574@lid",
            "body": "Move that task to project Mobile App",
            "type": "text",
        },
    }
    move_response = client.post("/webhooks/openwa?token=test-openwa", json=move)
    assert move_response.status_code == 200
    move_reply = move_response.json()["reply"]
    assert "Updated task" in move_reply
    assert "project Client Portal -> Mobile App" in move_reply

    priority = {
        "event": "message.received",
        "sessionId": "session",
        "data": {
            "waMessageId": "project-update-priority",
            "chatId": "102907500351574@lid",
            "from": "102907500351574@lid",
            "body": "Make that low priority",
            "type": "text",
        },
    }
    priority_response = client.post("/webhooks/openwa?token=test-openwa", json=priority)
    assert priority_response.status_code == 200
    priority_reply = priority_response.json()["reply"]
    assert "Updated task" in priority_reply
    assert "priority stayed Urgent" in priority_reply

    with SessionLocal() as db:
        lina = db.scalar(select(Person).where(Person.email == "lina@example.com"))
        assert lina is not None
        project = db.scalar(select(Project).where(Project.person_id == lina.id, Project.name == "Mobile App"))
        assert project is not None
        task = db.scalar(select(Task).where(Task.assigned_person_id == lina.id))
        assert task is not None
        assert task.project_id == project.id
        assert task.priority_order == 1


def test_openwa_plural_priority_update_changes_referenced_task_set(client: TestClient, monkeypatch: Any) -> None:
    with SessionLocal() as db:
        ali = Person(full_name="Ali Awdeh", email="ali@example.com")
        db.add(ali)
        db.flush()
        first = Task(title="Send rough email", assigned_person_id=ali.id, priority="medium")
        second = Task(title="Prepare rough report", assigned_person_id=ali.id, priority="low")
        db.add_all([first, second])
        db.flush()
        db.add(
            Conversation(
                whatsapp_chat_id="102907500351574@lid",
                contact_phone="102907500351574",
                state={"last_person_id": ali.id, "last_task_id": first.id, "last_task_ids": [first.id, second.id]},
            )
        )
        db.commit()
        first_id = first.id
        second_id = second.id

    _mock_planner(monkeypatch, [{"action_type": "update_task", "priority": "urgent", "confidence": 0.95}])

    event = {
        "event": "message.received",
        "sessionId": "session",
        "data": {
            "waMessageId": "plural-priority-update",
            "chatId": "102907500351574@lid",
            "from": "102907500351574@lid",
            "body": "Make them urgent",
            "type": "text",
        },
    }
    response = client.post("/webhooks/openwa?token=test-openwa", json=event)
    assert response.status_code == 200
    reply = response.json()["reply"]
    assert "Updated task" in reply
    assert "priority medium -> urgent" in reply
    assert "priority low -> urgent" in reply

    with SessionLocal() as db:
        assert db.get(Task, first_id).priority == "urgent"  # type: ignore[union-attr]
        assert db.get(Task, second_id).priority == "urgent"  # type: ignore[union-attr]


def test_openwa_plural_priority_update_preserves_existing_assignees(client: TestClient, monkeypatch: Any) -> None:
    with SessionLocal() as db:
        ali = Person(full_name="Ali Awdeh", email="ali@example.com")
        naji = Person(full_name="Naji", email="naji@example.com")
        nagy = Person(full_name="Nagy", email="nagy@example.com")
        db.add_all([ali, naji, nagy])
        db.flush()
        first = Task(title="First meeting task", assigned_person_id=ali.id, priority="medium")
        second = Task(title="Second meeting task", assigned_person_id=naji.id, priority="medium")
        db.add_all([first, second])
        db.flush()
        db.add(
            Conversation(
                whatsapp_chat_id="102907500351574@lid",
                contact_phone="102907500351574",
                state={"last_person_id": nagy.id, "last_task_id": second.id, "last_task_ids": [first.id, second.id]},
            )
        )
        db.commit()
        first_id = first.id
        second_id = second.id
        ali_id = ali.id
        naji_id = naji.id

    _mock_planner(monkeypatch, [{"action_type": "update_task", "priority": "urgent", "confidence": 0.95}])

    event = {
        "event": "message.received",
        "sessionId": "session",
        "data": {
            "waMessageId": "plural-priority-preserve-assignees",
            "chatId": "102907500351574@lid",
            "from": "102907500351574@lid",
            "body": "Make all of them urgent",
            "type": "text",
        },
    }
    response = client.post("/webhooks/openwa?token=test-openwa", json=event)
    assert response.status_code == 200
    reply = response.json()["reply"]
    assert "priority medium -> urgent" in reply
    assert "assignee" not in reply

    with SessionLocal() as db:
        first = db.get(Task, first_id)
        second = db.get(Task, second_id)
        assert first is not None
        assert second is not None
        assert first.priority == "urgent"
        assert second.priority == "urgent"
        assert first.assigned_person_id == ali_id
        assert second.assigned_person_id == naji_id


def test_openwa_numeric_project_priority_order_update_sends_project_list(client: TestClient, monkeypatch: Any) -> None:
    with SessionLocal() as db:
        ali = Person(full_name="Ali Awdeh", email="ali@example.com")
        db.add(ali)
        db.flush()
        project = Project(person_id=ali.id, name="Ops")
        db.add(project)
        db.flush()
        first = Task(title="First task", assigned_person_id=ali.id, project_id=project.id, priority_order=1)
        second = Task(title="Second task", assigned_person_id=ali.id, project_id=project.id, priority_order=2)
        db.add_all([first, second])
        db.flush()
        db.add(
            Conversation(
                whatsapp_chat_id="102907500351574@lid",
                contact_phone="102907500351574",
                state={"last_person_id": ali.id, "last_task_id": second.id, "last_task_ids": [first.id, second.id]},
            )
        )
        db.commit()
        first_id = first.id
        second_id = second.id

    _mock_planner(monkeypatch, [{"action_type": "update_task", "related_task_id": second_id, "priority_order": 1, "confidence": 0.95}])

    event = {
        "event": "message.received",
        "sessionId": "session",
        "data": {
            "waMessageId": "numeric-priority-order-update",
            "chatId": "102907500351574@lid",
            "from": "102907500351574@lid",
            "body": "Make the second task priority 1",
            "type": "text",
        },
    }
    response = client.post("/webhooks/openwa?token=test-openwa", json=event)
    assert response.status_code == 200
    reply = response.json()["reply"]
    assert "priority High -> Urgent" in reply

    with SessionLocal() as db:
        first = db.get(Task, first_id)
        second = db.get(Task, second_id)
        assert first is not None
        assert second is not None
        assert first.priority_order == 2
        assert second.priority_order == 1
        email = db.scalar(select(Email).where(Email.subject == "Task updated: Second task"))
        assert email is not None
        assert "Priority changed from High to Urgent" in email.text_body
        assert "Current priority list for Ops:" in email.text_body
        assert "Urgent: Second task (Ali Awdeh)" in email.text_body
        assert "High: First task (Ali Awdeh)" in email.text_body


def test_openwa_update_them_applies_previous_rewritten_titles(client: TestClient, monkeypatch: Any) -> None:
    with SessionLocal() as db:
        ali = Person(full_name="Ali Awdeh", email="ali@example.com")
        db.add(ali)
        db.flush()
        first = Task(title="Ali i just gave you his email address, send him an email woth his task", assigned_person_id=ali.id)
        second = Task(title="email a report to me and the ceo mario to work on some stuff he knows about", assigned_person_id=ali.id)
        db.add_all([first, second])
        db.flush()
        conversation = Conversation(
            whatsapp_chat_id="102907500351574@lid",
            contact_phone="102907500351574",
            state={"last_person_id": ali.id, "last_task_id": first.id, "last_task_ids": [first.id, second.id]},
        )
        db.add(conversation)
        db.flush()
        db.add(
            Message(
                external_message_id="assistant-rewrite-suggestions",
                conversation_id=conversation.id,
                direction="outbound",
                message_type="text",
                text=(
                    "Here are cleaner titles: 1. Send Ali an email with the details of his task. "
                    "2. Email a report to me and CEO Mario about the work he is already familiar with."
                ),
                raw_event={},
            )
        )
        db.commit()
        first_id = first.id
        second_id = second.id

    _mock_planner(
        monkeypatch,
        [
            {
                "action_type": "update_task",
                "related_task_id": first_id,
                "title": "Send Ali an email with the details of his task",
                "confidence": 0.95,
            },
            {
                "action_type": "update_task",
                "related_task_id": second_id,
                "title": "Email a report to me and CEO Mario about the work he is already familiar with",
                "confidence": 0.95,
            },
        ],
    )

    event = {
        "event": "message.received",
        "sessionId": "session",
        "data": {
            "waMessageId": "apply-rewritten-titles",
            "chatId": "102907500351574@lid",
            "from": "102907500351574@lid",
            "body": "Update them",
            "type": "text",
        },
    }
    response = client.post("/webhooks/openwa?token=test-openwa", json=event)
    assert response.status_code == 200
    reply = response.json()["reply"]
    assert "Updated task" in reply
    assert "Send Ali an email with the details of his task" in reply
    assert "Email a report to me and CEO Mario" in reply

    with SessionLocal() as db:
        assert db.get(Task, first_id).title == "Send Ali an email with the details of his task"  # type: ignore[union-attr]
        assert (
            db.get(Task, second_id).title
            == "Email a report to me and CEO Mario about the work he is already familiar with"
        )


def test_openwa_plural_status_update_changes_referenced_task_set(client: TestClient, monkeypatch: Any) -> None:
    with SessionLocal() as db:
        ali = Person(full_name="Ali Awdeh", email="ali@example.com")
        db.add(ali)
        db.flush()
        first = Task(title="First task", assigned_person_id=ali.id)
        second = Task(title="Second task", assigned_person_id=ali.id)
        db.add_all([first, second])
        db.flush()
        db.add(
            Conversation(
                whatsapp_chat_id="102907500351574@lid",
                contact_phone="102907500351574",
                state={"last_person_id": ali.id, "last_task_id": first.id, "last_task_ids": [first.id, second.id]},
            )
        )
        db.commit()
        first_id = first.id
        second_id = second.id

    _mock_planner(monkeypatch, [{"action_type": "update_task", "status": "in_progress", "confidence": 0.95}])

    event = {
        "event": "message.received",
        "sessionId": "session",
        "data": {
            "waMessageId": "plural-status-update",
            "chatId": "102907500351574@lid",
            "from": "102907500351574@lid",
            "body": "Set them in progress",
            "type": "text",
        },
    }
    response = client.post("/webhooks/openwa?token=test-openwa", json=event)
    assert response.status_code == 200
    reply = response.json()["reply"]
    assert "status open -> in_progress" in reply

    with SessionLocal() as db:
        assert db.get(Task, first_id).status == "in_progress"  # type: ignore[union-attr]
        assert db.get(Task, second_id).status == "in_progress"  # type: ignore[union-attr]


def test_openwa_rename_that_task_updates_existing_task(client: TestClient) -> None:
    with SessionLocal() as db:
        ali = Person(full_name="Ali Awdeh", email="ali@example.com")
        db.add(ali)
        db.flush()
        task = Task(title="Old title", assigned_person_id=ali.id)
        db.add(task)
        db.flush()
        db.add(
            Conversation(
                whatsapp_chat_id="102907500351574@lid",
                contact_phone="102907500351574",
                state={"last_person_id": ali.id, "last_task_id": task.id, "last_task_ids": [task.id]},
            )
        )
        db.commit()
        task_id = task.id

    event = {
        "event": "message.received",
        "sessionId": "session",
        "data": {
            "waMessageId": "rename-existing-task",
            "chatId": "102907500351574@lid",
            "from": "102907500351574@lid",
            "body": "Rename that task to Final QA title",
            "type": "text",
        },
    }
    response = client.post("/webhooks/openwa?token=test-openwa", json=event)
    assert response.status_code == 200
    assert "title \"Old title\" -> \"Final QA title\"" in response.json()["reply"]

    with SessionLocal() as db:
        tasks = list(db.scalars(select(Task)))
        assert len(tasks) == 1
        assert db.get(Task, task_id).title == "Final QA title"  # type: ignore[union-attr]


def test_openwa_missing_update_fields_asks_instead_of_reading_summary(client: TestClient, monkeypatch: Any) -> None:
    _mock_planner(
        monkeypatch,
        [
            {
                "action_type": "query_records",
                "query_target": "summary",
                "missing_fields": ["Which tasks and fields should I update?"],
                "confidence": 0.4,
            }
        ],
    )

    event = {
        "event": "message.received",
        "sessionId": "session",
        "data": {
            "waMessageId": "missing-update-fields",
            "chatId": "102907500351574@lid",
            "from": "102907500351574@lid",
            "body": "Update them",
            "type": "text",
        },
    }
    response = client.post("/webhooks/openwa?token=test-openwa", json=event)
    assert response.status_code == 200
    reply = response.json()["reply"]
    assert "Which tasks and fields should I update" in reply
    assert "I see" not in reply


def test_openwa_new_task_called_title_does_not_move_existing_project_context(client: TestClient) -> None:
    with SessionLocal() as db:
        ali = Person(full_name="Ali Awdeh", email="ali@example.com")
        db.add(ali)
        db.flush()
        existing_project = Project(person_id=ali.id, name="Existing Project")
        db.add(existing_project)
        db.flush()
        existing_task = Task(title="Existing task", assigned_person_id=ali.id, project_id=existing_project.id)
        db.add(existing_task)
        conversation = Conversation(
            whatsapp_chat_id="102907500351574@lid",
            contact_phone="102907500351574",
            state={"last_person_id": ali.id, "last_project_id": existing_project.id, "last_task_id": existing_task.id},
        )
        db.add(conversation)
        db.commit()

    event = {
        "event": "message.received",
        "sessionId": "session",
        "data": {
            "waMessageId": "create-called-title",
            "chatId": "102907500351574@lid",
            "from": "102907500351574@lid",
            "body": "create a new task for Ali called Travel Assist",
            "type": "text",
        },
    }
    response = client.post("/webhooks/openwa?token=test-openwa", json=event)
    assert response.status_code == 200
    assert "saved" in response.json()["reply"].lower()

    with SessionLocal() as db:
        ali = db.scalar(select(Person).where(Person.email == "ali@example.com"))
        assert ali is not None
        tasks = list(db.scalars(select(Task).where(Task.assigned_person_id == ali.id).order_by(Task.created_at.asc())))
        assert len(tasks) == 2
        assert tasks[0].title == "Existing task"
        assert tasks[0].project_id is not None
        assert tasks[1].title == "Travel Assist"
        assert tasks[1].project_id is None


def test_openwa_creates_named_person_and_project_without_email(client: TestClient) -> None:
    event = {
        "event": "message.received",
        "sessionId": "session",
        "data": {
            "waMessageId": "person-project-called",
            "chatId": "102907500351574@lid",
            "from": "102907500351574@lid",
            "body": "Create a new person called Darwish that have a project called Gulfmates.",
            "type": "text",
        },
    }
    response = client.post("/webhooks/openwa?token=test-openwa", json=event)
    assert response.status_code == 200
    reply = response.json()["reply"]
    assert "Darwish" in reply
    assert "Gulfmates" in reply

    with SessionLocal() as db:
        darwish = db.scalar(select(Person).where(Person.full_name == "Darwish"))
        assert darwish is not None
        project = db.scalar(select(Project).where(Project.person_id == darwish.id, Project.name == "Gulfmates"))
        assert project is not None


def test_openwa_creates_named_person_project_and_task(client: TestClient) -> None:
    event = {
        "event": "message.received",
        "sessionId": "session",
        "data": {
            "waMessageId": "person-project-task-called",
            "chatId": "102907500351574@lid",
            "from": "102907500351574@lid",
            "body": "Create a new person called Darwish with a project called Gulfmates and a task called prepare launch plan.",
            "type": "text",
        },
    }
    response = client.post("/webhooks/openwa?token=test-openwa", json=event)
    assert response.status_code == 200
    assert "task" in response.json()["reply"].lower()

    with SessionLocal() as db:
        darwish = db.scalar(select(Person).where(Person.full_name == "Darwish"))
        assert darwish is not None
        project = db.scalar(select(Project).where(Project.person_id == darwish.id, Project.name == "Gulfmates"))
        assert project is not None
        task = db.scalar(select(Task).where(Task.assigned_person_id == darwish.id, Task.title == "prepare launch plan"))
        assert task is not None
        assert task.project_id == project.id


def test_openwa_creates_responsible_person_project_task_and_remembers_context(client: TestClient) -> None:
    event = {
        "event": "message.received",
        "sessionId": "session",
        "data": {
            "waMessageId": "responsible-person-project-task",
            "chatId": "102907500351574@lid",
            "from": "102907500351574@lid",
            "body": (
                "Create a new person called Nagy is responsible for the project called Abu Dhabi maids, "
                "and give him a task of priority high to remove his eyeglasses."
            ),
            "type": "text",
        },
    }
    response = client.post("/webhooks/openwa?token=test-openwa", json=event)
    assert response.status_code == 200
    assert "task" in response.json()["reply"].lower()

    follow_up = {
        "event": "message.received",
        "sessionId": "session",
        "data": {
            "waMessageId": "responsible-person-project-task-followup",
            "chatId": "102907500351574@lid",
            "from": "102907500351574@lid",
            "body": "What tasks does he have?",
            "type": "text",
        },
    }
    follow_up_response = client.post("/webhooks/openwa?token=test-openwa", json=follow_up)
    assert follow_up_response.status_code == 200
    follow_up_reply = follow_up_response.json()["reply"]
    assert "Nagy" in follow_up_reply
    assert "Abu Dhabi maids" in follow_up_reply
    assert "remove his eyeglasses" in follow_up_reply
    assert "Urgent" in follow_up_reply

    with SessionLocal() as db:
        nagy = db.scalar(select(Person).where(Person.full_name == "Nagy"))
        assert nagy is not None
        project = db.scalar(select(Project).where(Project.person_id == nagy.id, Project.name == "Abu Dhabi maids"))
        assert project is not None
        task = db.scalar(select(Task).where(Task.assigned_person_id == nagy.id, Task.title == "remove his eyeglasses"))
        assert task is not None
        assert task.project_id == project.id
        assert task.priority == "high"
        conversation = db.scalar(select(Conversation).where(Conversation.whatsapp_chat_id == "102907500351574@lid"))
        assert conversation is not None
        assert conversation.state["last_person_id"] == nagy.id
        assert conversation.state["last_project_id"] == project.id
        assert conversation.state["last_task_id"] == task.id


def test_openwa_explicit_person_overrides_remembered_person_for_new_task(client: TestClient) -> None:
    with SessionLocal() as db:
        ali = Person(full_name="Ali Awdeh", email="ali@example.com")
        db.add(ali)
        db.flush()
        ali_project = Project(person_id=ali.id, name="Existing Ali Project")
        db.add(ali_project)
        db.flush()
        ali_task = Task(title="Existing Ali task", assigned_person_id=ali.id, project_id=ali_project.id)
        db.add(ali_task)
        db.add(
            Conversation(
                whatsapp_chat_id="102907500351574@lid",
                contact_phone="102907500351574",
                state={"last_person_id": ali.id, "last_project_id": ali_project.id, "last_task_id": ali_task.id},
            )
        )
        db.commit()

    event = {
        "event": "message.received",
        "sessionId": "session",
        "data": {
            "waMessageId": "explicit-person-overrides-memory",
            "chatId": "102907500351574@lid",
            "from": "102907500351574@lid",
            "body": (
                "Create a new person called Naji. He is responsible for the project called Abu Dhabi Maids, "
                "and give him a task of priority high to remove his eyeglasses."
            ),
            "type": "text",
        },
    }
    response = client.post("/webhooks/openwa?token=test-openwa", json=event)
    assert response.status_code == 200

    with SessionLocal() as db:
        ali = db.scalar(select(Person).where(Person.email == "ali@example.com"))
        naji = db.scalar(select(Person).where(Person.full_name == "Naji"))
        assert ali is not None
        assert naji is not None
        project = db.scalar(select(Project).where(Project.name == "Abu Dhabi Maids"))
        assert project is not None
        assert project.person_id == naji.id
        task = db.scalar(select(Task).where(Task.title == "remove his eyeglasses"))
        assert task is not None
        assert task.assigned_person_id == naji.id
        assert task.project_id == project.id
        assert task.assigned_person_id != ali.id
        conversation = db.scalar(select(Conversation).where(Conversation.whatsapp_chat_id == "102907500351574@lid"))
        assert conversation is not None
        assert conversation.state["last_person_id"] == naji.id


def test_openwa_responsible_for_project_phrase_creates_task_for_explicit_person(client: TestClient) -> None:
    with SessionLocal() as db:
        naji = Person(full_name="Naji")
        db.add(naji)
        db.flush()
        naji_project = Project(person_id=naji.id, name="Abu Dhabi Maids")
        db.add(naji_project)
        db.flush()
        naji_task = Task(title="Existing Naji task", assigned_person_id=naji.id, project_id=naji_project.id)
        db.add(naji_task)
        db.add(
            Conversation(
                whatsapp_chat_id="102907500351574@lid",
                contact_phone="102907500351574",
                state={"last_person_id": naji.id, "last_project_id": naji_project.id, "last_task_id": naji_task.id},
            )
        )
        db.commit()

    event = {
        "event": "message.received",
        "sessionId": "session",
        "data": {
            "waMessageId": "responsible-for-youssef-project-task",
            "chatId": "102907500351574@lid",
            "from": "102907500351574@lid",
            "body": "Create a new person called Youssef who is responsible for Bookers (Maze 80) hiring and Abu Dhabi project.",
            "type": "text",
        },
    }
    response = client.post("/webhooks/openwa?token=test-openwa", json=event)
    assert response.status_code == 200
    assert "task" in response.json()["reply"].lower()
    assert "Naji" not in response.json()["reply"]

    with SessionLocal() as db:
        naji = db.scalar(select(Person).where(Person.full_name == "Naji"))
        youssef = db.scalar(select(Person).where(Person.full_name == "Youssef"))
        assert naji is not None
        assert youssef is not None
        project = db.scalar(select(Project).where(Project.name == "Abu Dhabi"))
        assert project is not None
        assert project.person_id == youssef.id
        task = db.scalar(select(Task).where(Task.title == "Bookers (Maze 80) hiring"))
        assert task is not None
        assert task.assigned_person_id == youssef.id
        assert task.project_id == project.id
        assert task.assigned_person_id != naji.id
        conversation = db.scalar(select(Conversation).where(Conversation.whatsapp_chat_id == "102907500351574@lid"))
        assert conversation is not None
        assert conversation.state["last_person_id"] == youssef.id


def test_openwa_duplicate_event_is_idempotent(client: TestClient) -> None:
    headers = {"X-OpenWA-Token": "test-openwa"}
    first = client.post("/webhooks/openwa", json=_event(), headers=headers)
    second = client.post("/webhooks/openwa", json=_event(), headers=headers)
    assert first.status_code == 200
    assert first.json()["status"] == "processed"
    assert second.status_code == 200
    assert second.json()["status"] == "duplicate"
