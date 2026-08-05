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


def _flush_notifications(client: TestClient) -> dict[str, int]:
    response = client.post("/api/dashboard/tasks/notifications/flush")
    assert response.status_code == 200
    return response.json()


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

    first_task_response = client.post(
        "/api/dashboard/tasks",
        json={
            "title": "Review vendor shortlist",
            "assigned_person_id": person["id"],
            "project_id": project["id"],
            "priority": "medium",
        },
    )
    second_task_response = client.post(
        "/api/dashboard/tasks",
        json={
            "title": "Confirm vendor pricing",
            "assigned_person_id": person["id"],
            "project_id": project["id"],
            "priority": "medium",
        },
    )
    assert first_task_response.status_code == 200
    assert second_task_response.status_code == 200
    task = first_task_response.json()
    assert task["project_name"] == "Ops Cleanup"
    assert task["assigned_person_name"] == "Rami Mansour"
    assert _flush_notifications(client)["sent"] == 1

    priority_response = client.post(f"/api/dashboard/tasks/{task['id']}/priority", json={"priority": "urgent"})
    assert priority_response.status_code == 200
    assert priority_response.json()["priority_order"] == 1
    assert _flush_notifications(client)["sent"] == 1

    with SessionLocal() as db:
        email = db.scalar(select(Email).order_by(Email.created_at.desc()))
        assert email is not None
        assert email.subject == "Task priority changed: Review vendor shortlist"
        assert email.status == "queued"
        assert "Review vendor shortlist" in email.text_body
        assert "Priority changed from Unranked to Urgent" in email.text_body
        assert "Current project priority lists:" in email.text_body
        recipient = db.scalar(select(EmailRecipient).where(EmailRecipient.email_id == email.id))
        assert recipient is not None
        assert recipient.email_address == "rami@example.com"
        n8n_request = db.scalar(select(N8nRequest).where(N8nRequest.request_id == email.n8n_request_id))
        assert n8n_request is not None


def test_dashboard_project_priority_order_email_includes_full_project_list(client: TestClient) -> None:
    _login(client)
    person_response = client.post("/api/dashboard/people", json={"full_name": "Priority Owner", "email": "priority@example.com"})
    assert person_response.status_code == 200
    person = person_response.json()
    project_response = client.post("/api/dashboard/projects", json={"person_id": person["id"], "name": "Priority Project"})
    assert project_response.status_code == 200
    project = project_response.json()

    first = client.post(
        "/api/dashboard/tasks",
        json={"title": "First item", "assigned_person_id": person["id"], "project_id": project["id"], "priority_order": 1},
    )
    second = client.post(
        "/api/dashboard/tasks",
        json={"title": "Second item", "assigned_person_id": person["id"], "project_id": project["id"], "priority_order": 2},
    )
    third = client.post(
        "/api/dashboard/tasks",
        json={"title": "Third item", "assigned_person_id": person["id"], "project_id": project["id"], "priority_order": 3},
    )
    assert first.status_code == 200
    assert second.status_code == 200
    assert third.status_code == 200
    assert first.json()["priority_order"] == 1
    assert second.json()["priority_order"] == 2
    assert third.json()["priority_order"] == 3
    assert _flush_notifications(client)["sent"] == 1

    reordered = client.put(f"/api/dashboard/tasks/{third.json()['id']}", json={"priority_order": 1})
    assert reordered.status_code == 200
    assert reordered.json()["priority_order"] == 1
    assert _flush_notifications(client)["sent"] == 1

    with SessionLocal() as db:
        rows = list(db.scalars(select(Task).where(Task.project_id == project["id"]).order_by(Task.priority_order.asc())))
        assert [(task.title, task.priority_order) for task in rows] == [
            ("Third item", 1),
            ("First item", 2),
            ("Second item", 3),
        ]
        email = db.scalar(select(Email).order_by(Email.created_at.desc()))
        assert email is not None
        assert email.subject == "Task priority changed: Third item"
        assert "Priority changed from Medium to Urgent" in email.text_body
        assert "Current project priority lists:" in email.text_body
        assert "Urgent: Third item (Priority Owner)" in email.text_body
        assert "High: First item (Priority Owner)" in email.text_body
        assert "Medium: Second item (Priority Owner)" in email.text_body


def test_dashboard_task_creation_email_includes_project_priority_list(client: TestClient) -> None:
    _login(client)
    person_response = client.post("/api/dashboard/people", json={"full_name": "Create Notify", "email": "create-notify@example.com"})
    assert person_response.status_code == 200
    person = person_response.json()
    project_response = client.post("/api/dashboard/projects", json={"person_id": person["id"], "name": "Creation Project"})
    assert project_response.status_code == 200
    project = project_response.json()

    task_response = client.post(
        "/api/dashboard/tasks",
        json={"title": "Creation email task", "assigned_person_id": person["id"], "project_id": project["id"], "priority": "high"},
    )
    assert task_response.status_code == 200
    assert task_response.json()["priority_order"] is None
    pending_response = client.get("/api/dashboard/tasks/notifications/pending")
    assert pending_response.status_code == 200
    assert pending_response.json()["pending"] == 1

    with SessionLocal() as db:
        email = db.scalar(select(Email))
        assert email is None

    assert _flush_notifications(client)["sent"] == 1

    with SessionLocal() as db:
        email = db.scalar(select(Email))
        assert email is not None
        assert email.subject == "Task created: Creation email task"
        assert email.status == "queued"
        assert "Created task." in email.text_body
        assert "Creation email task" in email.text_body
        assert "Project: Creation Project" in email.text_body
        assert "Priority: Unranked" in email.text_body
        assert "Current project priority lists:" not in email.text_body
        assert "Task created: Creation email task" in email.html_body
        recipient = db.scalar(select(EmailRecipient).where(EmailRecipient.email_id == email.id))
        assert recipient is not None
        assert recipient.email_address == "create-notify@example.com"


def test_dashboard_updates_people_projects_and_moves_tasks(client: TestClient) -> None:
    _login(client)
    person_response = client.post("/api/dashboard/people", json={"full_name": "Nour Haddad", "email": "nour@example.com"})
    assert person_response.status_code == 200
    person = person_response.json()
    updated_person = client.put(
        f"/api/dashboard/people/{person['id']}",
        json={"full_name": "Nour Haddad", "company": "babysitting", "email": "nour@example.com"},
    )
    assert updated_person.status_code == 200
    assert updated_person.json()["company"] == "babysitting"

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
    assert _flush_notifications(client)["sent"] == 1

    moved_task = client.put(
        f"/api/dashboard/tasks/{task['id']}",
        json={"project_id": second_project.json()["id"], "priority": "high", "status": "in_progress"},
    )
    assert moved_task.status_code == 200
    assert moved_task.json()["project_name"] == "Retention"
    assert moved_task.json()["priority_order"] == 1
    assert _flush_notifications(client)["sent"] == 1

    with SessionLocal() as db:
        stored_task = db.get(Task, task["id"])
        assert stored_task is not None
        assert stored_task.project_id == second_project.json()["id"]
        project = db.scalar(select(Project).where(Project.name == "Launch Plan"))
        assert project is not None
        email = db.scalar(select(Email).order_by(Email.created_at.desc()))
        assert email is not None
        assert email.subject == "Task priority, project and status changed: Send stakeholder summary"
        assert "Project changed from Launch Plan to Retention" in email.text_body


def test_dashboard_notification_settings_disable_task_change_email(client: TestClient) -> None:
    _login(client)
    settings_response = client.get("/api/dashboard/settings/notifications")
    assert settings_response.status_code == 200
    assert settings_response.json()["task_change_email_notifications"] is True

    update_settings = client.put("/api/dashboard/settings/notifications", json={"task_change_email_notifications": False})
    assert update_settings.status_code == 200
    assert update_settings.json()["task_change_email_notifications"] is False

    person_response = client.post("/api/dashboard/people", json={"full_name": "No Email Test", "email": "notify@example.com"})
    assert person_response.status_code == 200
    person = person_response.json()
    task_response = client.post(
        "/api/dashboard/tasks",
        json={"title": "Notification toggle task", "assigned_person_id": person["id"], "priority": "medium"},
    )
    assert task_response.status_code == 200
    task = task_response.json()

    priority_response = client.post(f"/api/dashboard/tasks/{task['id']}/priority", json={"priority": "urgent"})
    assert priority_response.status_code == 200
    assert _flush_notifications(client)["sent"] == 0

    with SessionLocal() as db:
        email = db.scalar(select(Email))
        assert email is None


def test_dashboard_task_title_and_completion_email_notifications(client: TestClient) -> None:
    _login(client)
    person_response = client.post("/api/dashboard/people", json={"full_name": "Dana Salem", "email": "dana@example.com"})
    assert person_response.status_code == 200
    person = person_response.json()
    task_response = client.post(
        "/api/dashboard/tasks",
        json={"title": "Old dashboard task title", "assigned_person_id": person["id"], "priority": "medium"},
    )
    assert task_response.status_code == 200
    task = task_response.json()
    assert _flush_notifications(client)["sent"] == 1

    title_response = client.put(f"/api/dashboard/tasks/{task['id']}", json={"title": "New dashboard task title"})
    assert title_response.status_code == 200
    complete_response = client.post(f"/api/dashboard/tasks/{task['id']}/complete")
    assert complete_response.status_code == 200
    assert _flush_notifications(client)["sent"] == 1

    with SessionLocal() as db:
        update_email = db.scalar(select(Email).order_by(Email.created_at.desc()))
        assert update_email is not None
        assert update_email.subject == "Task status and title changed: New dashboard task title"
        assert "Title changed from Old dashboard task title to New dashboard task title" in update_email.text_body
        assert "Status changed from open to completed" in update_email.text_body


def test_dashboard_task_notification_digest_removes_redundant_priority_changes(client: TestClient) -> None:
    _login(client)
    person_response = client.post("/api/dashboard/people", json={"full_name": "Redundant Priority", "email": "redundant@example.com"})
    assert person_response.status_code == 200
    person = person_response.json()
    task_response = client.post(
        "/api/dashboard/tasks",
        json={"title": "Priority swings", "assigned_person_id": person["id"], "priority": "low"},
    )
    assert task_response.status_code == 200
    task = task_response.json()
    assert _flush_notifications(client)["sent"] == 1

    for priority in ["high", "low", "medium", "urgent"]:
        response = client.put(f"/api/dashboard/tasks/{task['id']}", json={"priority": priority})
        assert response.status_code == 200
    assert _flush_notifications(client)["sent"] == 1

    with SessionLocal() as db:
        email = db.scalar(select(Email).order_by(Email.created_at.desc()))
        assert email is not None
        assert email.subject == "Task priority changed: Priority swings"
        assert "Priority changed from Low to Urgent" in email.text_body
        assert "Priority changed from High" not in email.text_body
        assert "Priority changed from Medium" not in email.text_body


def test_dashboard_delete_task_queues_email_and_normalizes_project_priority(client: TestClient) -> None:
    _login(client)
    person_response = client.post("/api/dashboard/people", json={"full_name": "Delete Notify", "email": "delete-notify@example.com"})
    assert person_response.status_code == 200
    person = person_response.json()
    project_response = client.post("/api/dashboard/projects", json={"person_id": person["id"], "name": "Deletion Project"})
    assert project_response.status_code == 200
    project = project_response.json()
    first = client.post(
        "/api/dashboard/tasks",
        json={"title": "Delete this task", "assigned_person_id": person["id"], "project_id": project["id"], "priority_order": 1},
    )
    second = client.post(
        "/api/dashboard/tasks",
        json={"title": "Keep this task", "assigned_person_id": person["id"], "project_id": project["id"], "priority_order": 2},
    )
    assert first.status_code == 200
    assert second.status_code == 200
    assert _flush_notifications(client)["sent"] == 1

    delete_response = client.delete(f"/api/dashboard/tasks/{first.json()['id']}")
    assert delete_response.status_code == 200
    assert delete_response.json()["deleted"] is True
    assert _flush_notifications(client)["sent"] == 1

    with SessionLocal() as db:
        assert db.get(Task, first.json()["id"]) is None
        kept = db.get(Task, second.json()["id"])
        assert kept is not None
        assert kept.priority_order == 1
        email = db.scalar(select(Email).order_by(Email.created_at.desc()))
        assert email is not None
        assert email.subject == "Task deleted: Delete this task"
        assert "Deleted task." in email.text_body
        assert "Was in project: Deletion Project" in email.text_body


def test_dashboard_project_update_and_delete_notify_related_people(client: TestClient) -> None:
    _login(client)
    person_response = client.post("/api/dashboard/people", json={"full_name": "Project Notify", "email": "project-notify@example.com"})
    assert person_response.status_code == 200
    person = person_response.json()
    project_response = client.post("/api/dashboard/projects", json={"person_id": person["id"], "name": "Old Project"})
    assert project_response.status_code == 200
    project = project_response.json()
    task_response = client.post(
        "/api/dashboard/tasks",
        json={"title": "Project attached task", "assigned_person_id": person["id"], "project_id": project["id"], "priority_order": 1},
    )
    assert task_response.status_code == 200
    assert _flush_notifications(client)["sent"] == 1

    rename_response = client.put(f"/api/dashboard/projects/{project['id']}", json={"name": "Renamed Project", "status": "paused"})
    assert rename_response.status_code == 200
    assert _flush_notifications(client)["sent"] == 1

    with SessionLocal() as db:
        email = db.scalar(select(Email).order_by(Email.created_at.desc()))
        assert email is not None
        assert email.subject == "Task updated: Project attached task"
        assert "Project renamed from Old Project to Renamed Project" in email.text_body
        assert "Project status changed from active to paused" in email.text_body

    delete_response = client.delete(f"/api/dashboard/projects/{project['id']}")
    assert delete_response.status_code == 200
    assert delete_response.json() == {"deleted": True, "detached_tasks": 1}
    assert _flush_notifications(client)["sent"] == 1

    with SessionLocal() as db:
        assert db.get(Project, project["id"]) is None
        task = db.get(Task, task_response.json()["id"])
        assert task is not None
        assert task.project_id is None
        assert task.priority_order == 0
        email = db.scalar(select(Email).order_by(Email.created_at.desc()))
        assert email is not None
        assert "Project changed from Renamed Project to No project" in email.text_body
