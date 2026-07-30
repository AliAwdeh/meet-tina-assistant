from fastapi.testclient import TestClient
from sqlalchemy import select

from app.auth.passwords import hash_password
from app.core.database import SessionLocal
from app.models.entities import Email, EmailRecipient, N8nRequest, User


def _login(client: TestClient) -> None:
    with SessionLocal() as db:
        db.add(
            User(
                name="Dashboard User",
                email="dashboard@example.com",
                role="admin",
                password_hash=hash_password("super-secret-pass"),
            )
        )
        db.commit()
    response = client.post("/api/auth/login", json={"email": "dashboard@example.com", "password": "super-secret-pass"})
    assert response.status_code == 200


def test_dashboard_projects_and_priority_change_email(client: TestClient) -> None:
    _login(client)
    person_response = client.post(
        "/api/dashboard/people",
        json={"full_name": "Rami Mansour", "email": "rami@example.com"},
    )
    assert person_response.status_code == 200
    person = person_response.json()

    project_response = client.post(
        "/api/dashboard/projects",
        json={"person_id": person["id"], "name": "Ops Cleanup", "description": "Internal operations improvements"},
    )
    assert project_response.status_code == 200
    project = project_response.json()
    assert project["person_name"] == "Rami Mansour"

    task_response = client.post(
        "/api/dashboard/tasks",
        json={
            "title": "Confirm vendor pricing",
            "assigned_person_id": person["id"],
            "project_id": project["id"],
            "priority": "medium",
        },
    )
    assert task_response.status_code == 200
    task = task_response.json()
    assert task["project_name"] == "Ops Cleanup"
    assert task["assigned_person_name"] == "Rami Mansour"

    priority_response = client.post(f"/api/dashboard/tasks/{task['id']}/priority", json={"priority": "urgent"})
    assert priority_response.status_code == 200
    assert priority_response.json()["priority"] == "urgent"

    with SessionLocal() as db:
        email = db.scalar(select(Email).where(Email.subject == "Task priority changed: Confirm vendor pricing"))
        assert email is not None
        assert email.status == "queued"
        assert "changed from medium to urgent" in email.text_body
        recipient = db.scalar(select(EmailRecipient).where(EmailRecipient.email_id == email.id))
        assert recipient is not None
        assert recipient.email_address == "rami@example.com"
        n8n_request = db.scalar(select(N8nRequest).where(N8nRequest.request_id == email.n8n_request_id))
        assert n8n_request is not None
