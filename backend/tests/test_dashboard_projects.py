from fastapi.testclient import TestClient
from sqlalchemy import select

from app.auth.passwords import hash_password
from app.core.database import SessionLocal
from app.models.entities import Email, EmailRecipient, N8nRequest, Project, Task, User


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


def test_dashboard_updates_people_projects_and_moves_tasks(client: TestClient) -> None:
    _login(client)
    person_response = client.post("/api/dashboard/people", json={"full_name": "Nour Haddad", "email": "nour@example.com"})
    assert person_response.status_code == 200
    person = person_response.json()
    updated_person = client.put(
        f"/api/dashboard/people/{person['id']}",
        json={"full_name": "Nour Haddad", "company": "Meet Tina", "email": "nour@example.com"},
    )
    assert updated_person.status_code == 200
    assert updated_person.json()["company"] == "Meet Tina"

    first_project = client.post("/api/dashboard/projects", json={"person_id": person["id"], "name": "Launch"})
    second_project = client.post("/api/dashboard/projects", json={"person_id": person["id"], "name": "Retention"})
    assert first_project.status_code == 200
    assert second_project.status_code == 200
    renamed_project = client.put(
        f"/api/dashboard/projects/{first_project.json()['id']}",
        json={"name": "Launch Plan", "status": "active"},
    )
    assert renamed_project.status_code == 200
    assert renamed_project.json()["name"] == "Launch Plan"

    task_response = client.post(
        "/api/dashboard/tasks",
        json={
            "title": "Send stakeholder summary",
            "assigned_person_id": person["id"],
            "project_id": first_project.json()["id"],
            "priority": "low",
        },
    )
    assert task_response.status_code == 200
    task = task_response.json()

    moved_task = client.put(
        f"/api/dashboard/tasks/{task['id']}",
        json={"project_id": second_project.json()["id"], "priority": "high", "status": "in_progress"},
    )
    assert moved_task.status_code == 200
    assert moved_task.json()["project_name"] == "Retention"
    assert moved_task.json()["priority"] == "high"

    with SessionLocal() as db:
        stored_task = db.get(Task, task["id"])
        assert stored_task is not None
        assert stored_task.project_id == second_project.json()["id"]
        project = db.scalar(select(Project).where(Project.name == "Launch Plan"))
        assert project is not None
        email = db.scalar(select(Email).where(Email.subject == "Task updated: Send stakeholder summary"))
        assert email is not None
        assert "Project changed from Launch Plan to Retention" in email.text_body
        assert "Priority changed from low to high" in email.text_body
