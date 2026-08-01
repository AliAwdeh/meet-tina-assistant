from copy import deepcopy
from datetime import UTC, datetime
from html import escape
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from app.agent.tools.registry import ToolContext, send_email_tool
from app.auth.dependencies import get_current_user
from app.core.config import Settings, get_settings
from app.core.database import get_db
from app.models.entities import AppSetting, Person, Project, Task, User
from app.repositories import records
from app.schemas.domain import (
    DashboardSummary,
    MeetingCreate,
    MeetingRead,
    NotificationSettings,
    NotificationSettingsRead,
    PersonCreate,
    PersonRead,
    PersonUpdate,
    ProjectCreate,
    ProjectRead,
    ProjectUpdate,
    ReminderCreate,
    ReminderRead,
    TaskCreate,
    TaskPriorityUpdate,
    TaskRead,
    TaskUpdate,
)
from app.services import dashboard as dashboard_service
from app.services.audit import write_audit

router = APIRouter(dependencies=[Depends(get_current_user)])

NOTIFICATION_SETTINGS_KEY = "notification_settings"
TASK_NOTIFICATION_BATCHES_KEY = "task_notification_batches"
PROJECT_PRIORITY_NAMES = {1: "Urgent", 2: "High", 3: "Medium", 4: "Low"}
PROJECT_PRIORITY_ORDERS = {"urgent": 1, "high": 2, "medium": 3, "low": 4}


def _notification_settings(db: Session) -> NotificationSettingsRead:
    row = db.get(AppSetting, NOTIFICATION_SETTINGS_KEY)
    value = row.value if row is not None else {}
    settings = NotificationSettings.model_validate(value)
    return NotificationSettingsRead(**settings.model_dump())


def _task_change_emails_enabled(db: Session) -> bool:
    return _notification_settings(db).task_change_email_notifications


@router.get("/settings/notifications", response_model=NotificationSettingsRead)
def notification_settings(db: Session = Depends(get_db)) -> NotificationSettingsRead:
    return _notification_settings(db)


@router.put("/settings/notifications", response_model=NotificationSettingsRead)
def update_notification_settings(
    payload: NotificationSettings,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> NotificationSettingsRead:
    row = db.get(AppSetting, NOTIFICATION_SETTINGS_KEY)
    if row is None:
        row = AppSetting(key=NOTIFICATION_SETTINGS_KEY, value=payload.model_dump())
        db.add(row)
    else:
        row.value = payload.model_dump()
    write_audit(
        db,
        actor_type="dashboard_user",
        actor_id=user.id,
        action="update_notification_settings",
        entity_type="app_setting",
        entity_id=NOTIFICATION_SETTINGS_KEY,
        safe_metadata=payload.model_dump(),
    )
    db.commit()
    return _notification_settings(db)


@router.get("/summary", response_model=DashboardSummary)
def summary(db: Session = Depends(get_db)) -> DashboardSummary:
    return dashboard_service.get_summary(db)


@router.get("/people", response_model=list[PersonRead])
def people(q: str | None = None, db: Session = Depends(get_db)) -> list[PersonRead]:
    return [PersonRead.model_validate(person, from_attributes=True) for person in records.search_people(db, q)]


@router.post("/people", response_model=PersonRead)
def create_person(
    payload: PersonCreate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> PersonRead:
    person = records.create_person(db, payload)
    write_audit(db, actor_type="dashboard_user", actor_id=user.id, action="create_person", entity_type="person", entity_id=person.id)
    db.commit()
    db.refresh(person)
    return PersonRead.model_validate(person, from_attributes=True)


@router.put("/people/{person_id}", response_model=PersonRead)
def update_person(
    person_id: str,
    payload: PersonUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> PersonRead:
    person = db.get(Person, person_id)
    if person is None:
        raise HTTPException(status_code=404, detail="Person not found")
    for field in payload.model_fields_set:
        setattr(person, field, getattr(payload, field))
    write_audit(db, actor_type="dashboard_user", actor_id=user.id, action="update_person", entity_type="person", entity_id=person.id)
    db.commit()
    db.refresh(person)
    return PersonRead.model_validate(person, from_attributes=True)


def _project_read(db: Session, project: Project) -> ProjectRead:
    person = db.get(Person, project.person_id)
    return ProjectRead(
        id=project.id,
        person_id=project.person_id,
        person_name=person.full_name if person else None,
        name=project.name,
        description=project.description,
        status=project.status,  # type: ignore[arg-type]
        created_at=project.created_at,
        updated_at=project.updated_at,
    )


def _task_read(db: Session, task: Task) -> TaskRead:
    person = db.get(Person, task.assigned_person_id) if task.assigned_person_id else None
    project = db.get(Project, task.project_id) if task.project_id else None
    return TaskRead(
        id=task.id,
        title=task.title,
        description=task.description,
        priority=task.priority,  # type: ignore[arg-type]
        priority_order=task.priority_order or None,
        assigned_person_id=task.assigned_person_id,
        assigned_person_name=person.full_name if person else None,
        project_id=task.project_id,
        project_name=project.name if project else None,
        due_date=task.due_date,
        related_meeting_id=task.related_meeting_id,
        status=task.status,
        completed_at=task.completed_at,
        created_at=task.created_at,
        updated_at=task.updated_at,
    )


def _ordered_project_tasks(db: Session, project_id: str) -> list[Task]:
    tasks = list(
        db.scalars(
            select(Task)
            .where(Task.project_id == project_id, Task.status.not_in(["completed", "cancelled"]))
            .order_by(Task.created_at.asc())
        )
    )
    return sorted(tasks, key=lambda task: (task.priority_order if task.priority_order > 0 else 1_000_000, task.created_at))


def _project_priority_name(priority_order: int | None) -> str:
    if priority_order is None or priority_order <= 0:
        return "Unranked"
    return PROJECT_PRIORITY_NAMES.get(priority_order, str(priority_order))


def _project_priority_order(priority: str | None) -> int | None:
    return PROJECT_PRIORITY_ORDERS.get(priority or "")


def _task_priority_name(task: Task, project_id: str | None = None) -> str:
    if project_id or task.project_id:
        return _project_priority_name(task.priority_order)
    return task.priority.title()


def _normalize_project_priority_sequence(
    db: Session,
    project_id: str | None,
    *,
    focused_task: Task | None = None,
    desired_order: int | None = None,
) -> None:
    if not project_id:
        return
    tasks = _ordered_project_tasks(db, project_id)
    if focused_task is not None and focused_task in tasks and desired_order is not None:
        tasks = [task for task in tasks if task.id != focused_task.id]
        index = max(0, min(desired_order - 1, len(tasks)))
        tasks.insert(index, focused_task)
    for index, task in enumerate(tasks, start=1):
        task.priority_order = index
    db.flush()


def _project_priority_list(db: Session, project_id: str | None) -> str:
    if not project_id:
        return ""
    project = db.get(Project, project_id)
    if project is None:
        return ""
    tasks = _ordered_project_tasks(db, project_id)
    if not tasks:
        return ""
    lines = [f"Current priority list for {project.name}:"]
    for task in tasks:
        assignee = db.get(Person, task.assigned_person_id) if task.assigned_person_id else None
        assignee_label = f" ({assignee.full_name})" if assignee else ""
        lines.append(f"{_project_priority_name(task.priority_order)}: {task.title}{assignee_label}")
    return "\n".join(lines)


def _task_email_context(db: Session, task: Task) -> str:
    person = db.get(Person, task.assigned_person_id) if task.assigned_person_id else None
    project = db.get(Project, task.project_id) if task.project_id else None
    return "\n".join(
        [
            f"Task: {task.title}",
            f"Project: {project.name if project else 'No project'}",
            f"Related person: {person.full_name if person else 'Unassigned'}",
            f"Priority: {_task_priority_name(task)}",
        ]
    )


def _date_value(value: datetime | None) -> str | None:
    return value.date().isoformat() if value else None


def _task_snapshot(db: Session, task: Task) -> dict[str, Any]:
    person = db.get(Person, task.assigned_person_id) if task.assigned_person_id else None
    project = db.get(Project, task.project_id) if task.project_id else None
    return {
        "id": task.id,
        "title": task.title,
        "description": task.description,
        "status": task.status,
        "priority": task.priority,
        "priority_order": task.priority_order,
        "priority_label": _task_priority_name(task),
        "assigned_person_id": task.assigned_person_id,
        "assigned_person_name": person.full_name if person else "Unassigned",
        "project_id": task.project_id,
        "project_name": project.name if project else "No project",
        "due_date": _date_value(task.due_date),
    }


def _pending_batches(db: Session) -> tuple[AppSetting, dict[str, Any]]:
    row = db.get(AppSetting, TASK_NOTIFICATION_BATCHES_KEY)
    if row is None:
        row = AppSetting(key=TASK_NOTIFICATION_BATCHES_KEY, value={"users": {}})
        db.add(row)
        db.flush()
    value = deepcopy(row.value or {})
    value.setdefault("users", {})
    return row, value


def _queue_task_notification(
    db: Session,
    user: User,
    task: Task,
    *,
    action: str,
    before: dict[str, Any] | None = None,
    old_project_id: str | None = None,
    old_assigned_person_id: str | None = None,
) -> None:
    if not _task_change_emails_enabled(db):
        return
    people = _task_notification_people(db, task, old_project_id=old_project_id, old_assigned_person_id=old_assigned_person_id)
    if not any(person.email for person in people):
        return
    after = _task_snapshot(db, task)
    row, value = _pending_batches(db)
    users = value.setdefault("users", {})
    batch = users.setdefault(user.id, {"tasks": {}})
    tasks = batch.setdefault("tasks", {})
    entry = tasks.get(task.id)
    recipient_ids = {person.id for person in people}
    project_ids = {project_id for project_id in [old_project_id, task.project_id] if project_id}
    if entry is None:
        tasks[task.id] = {
            "action": action,
            "before": before,
            "after": after,
            "recipient_ids": sorted(recipient_ids),
            "project_ids": sorted(project_ids),
            "created_at": datetime.now(UTC).isoformat(),
        }
    else:
        entry["after"] = after
        entry["recipient_ids"] = sorted(set(entry.get("recipient_ids", [])) | recipient_ids)
        entry["project_ids"] = sorted(set(entry.get("project_ids", [])) | project_ids)
        if entry.get("action") != "created":
            entry["action"] = action
    row.value = value
    flag_modified(row, "value")
    db.flush()


def _snapshot_priority(snapshot: dict[str, Any]) -> str:
    project_id = snapshot.get("project_id")
    if project_id:
        return _project_priority_name(int(snapshot.get("priority_order") or 0))
    return str(snapshot.get("priority") or "medium").title()


def _change_lines(entry: dict[str, Any]) -> list[str]:
    action = entry.get("action")
    before = entry.get("before") or {}
    after = entry.get("after") or {}
    if action == "created":
        return [
            "Created task.",
            f"Assigned to {after.get('assigned_person_name', 'Unassigned')}.",
            f"Project: {after.get('project_name', 'No project')}.",
            f"Priority: {_snapshot_priority(after)}.",
        ]
    lines: list[str] = []
    if before.get("title") != after.get("title"):
        lines.append(f"Title changed from {before.get('title')} to {after.get('title')}.")
    if before.get("description") != after.get("description"):
        lines.append("Description updated.")
    if before.get("project_id") != after.get("project_id"):
        lines.append(f"Project changed from {before.get('project_name', 'No project')} to {after.get('project_name', 'No project')}.")
    if before.get("assigned_person_id") != after.get("assigned_person_id"):
        lines.append(
            f"Assignee changed from {before.get('assigned_person_name', 'Unassigned')} "
            f"to {after.get('assigned_person_name', 'Unassigned')}."
        )
    if before.get("status") != after.get("status"):
        lines.append(f"Status changed from {before.get('status')} to {after.get('status')}.")
    if before.get("due_date") != after.get("due_date"):
        lines.append(f"Due date changed from {before.get('due_date') or 'none'} to {after.get('due_date') or 'none'}.")
    before_priority = _snapshot_priority(before) if before else ""
    after_priority = _snapshot_priority(after)
    if before_priority != after_priority:
        lines.append(f"Priority changed from {before_priority} to {after_priority}.")
    return lines


def _project_priority_items(db: Session, project_id: str) -> list[dict[str, str]]:
    project = db.get(Project, project_id)
    if project is None:
        return []
    items: list[dict[str, str]] = []
    for task in _ordered_project_tasks(db, project_id):
        assignee = db.get(Person, task.assigned_person_id) if task.assigned_person_id else None
        items.append(
            {
                "project": project.name,
                "priority": _project_priority_name(task.priority_order),
                "title": task.title,
                "assignee": assignee.full_name if assignee else "Unassigned",
            }
        )
    return items


def _render_task_digest(db: Session, recipient: Person, entries: list[dict[str, Any]]) -> tuple[str, str]:
    changed_entries = [(entry, _change_lines(entry)) for entry in entries]
    changed_entries = [(entry, lines) for entry, lines in changed_entries if lines]
    project_ids = sorted(
        {
            project_id
            for entry, _lines in changed_entries
            for project_id in entry.get("project_ids", [])
            if project_id
        }
    )
    text_lines = [f"Hi {recipient.full_name},", "", "Here are the latest task updates:", ""]
    html_parts = [
        "<div style=\"font-family:Inter,Arial,sans-serif;background:#f7f4ee;padding:24px;color:#1f2a24;\">",
        "<div style=\"max-width:720px;margin:0 auto;background:#ffffff;border:1px solid #e7e0d7;border-radius:10px;overflow:hidden;\">",
        "<div style=\"border-left:6px solid #88c7a2;padding:22px 24px;\">",
        "<p style=\"margin:0 0 6px;color:#4d8f69;font-size:13px;font-weight:700;letter-spacing:.02em;\">Meet Tina</p>",
        f"<h1 style=\"margin:0;font-size:22px;line-height:1.25;\">Task update digest</h1>",
        f"<p style=\"margin:8px 0 0;color:#6b6258;font-size:14px;\">Hi {escape(recipient.full_name)}, here are the latest task updates.</p>",
        "</div>",
        "<div style=\"padding:0 24px 22px;\">",
    ]
    by_project: dict[str, list[tuple[dict[str, Any], list[str]]]] = {}
    for entry, lines in changed_entries:
        after = entry.get("after") or {}
        by_project.setdefault(str(after.get("project_name") or "No project"), []).append((entry, lines))
    for project_name, project_entries in by_project.items():
        text_lines.append(f"{project_name}")
        html_parts.append(
            f"<section style=\"margin-top:18px;border:1px solid #ebe5dc;border-radius:8px;overflow:hidden;\">"
            f"<div style=\"background:#f8f6f1;padding:10px 14px;font-weight:700;\">{escape(project_name)}</div>"
            f"<div style=\"padding:12px 14px;\">"
        )
        for entry, lines in project_entries:
            after = entry.get("after") or {}
            text_lines.append(f"- {after.get('title')}")
            html_parts.append(
                f"<div style=\"border-bottom:1px solid #f0ece6;padding:10px 0;\">"
                f"<div style=\"font-weight:700;margin-bottom:6px;\">{escape(str(after.get('title') or 'Untitled task'))}</div>"
                f"<ul style=\"margin:0;padding-left:18px;color:#4b443d;font-size:14px;line-height:1.6;\">"
            )
            for line in lines:
                text_lines.append(f"  - {line}")
                html_parts.append(f"<li>{escape(line)}</li>")
            html_parts.append("</ul></div>")
        html_parts.append("</div></section>")
        text_lines.append("")
    if project_ids:
        text_lines.append("Current project priority lists:")
        html_parts.append("<h2 style=\"margin:24px 0 10px;font-size:17px;\">Current project priority lists</h2>")
        for project_id in project_ids:
            items = _project_priority_items(db, project_id)
            if not items:
                continue
            project_name = items[0]["project"]
            text_lines.append(project_name)
            html_parts.append(
                f"<section style=\"margin-top:12px;border:1px solid #d8e9dd;border-radius:8px;background:#fbfdfb;padding:14px;\">"
                f"<div style=\"font-weight:700;margin-bottom:10px;color:#2f6b46;\">{escape(project_name)}</div>"
            )
            for item in items:
                text_lines.append(f"- {item['priority']}: {item['title']} ({item['assignee']})")
                html_parts.append(
                    f"<div style=\"display:flex;gap:10px;padding:7px 0;border-top:1px solid #edf4ee;\">"
                    f"<span style=\"min-width:72px;font-weight:700;color:#1f2a24;\">{escape(item['priority'])}</span>"
                    f"<span>{escape(item['title'])} <span style=\"color:#7a7168;\">({escape(item['assignee'])})</span></span>"
                    f"</div>"
                )
            html_parts.append("</section>")
            text_lines.append("")
    html_parts.extend(["</div>", "</div>", "</div>"])
    return "\n".join(text_lines).strip(), "".join(html_parts)


@router.get("/tasks/notifications/pending")
def pending_task_notifications(db: Session = Depends(get_db), user: User = Depends(get_current_user)) -> dict[str, int]:
    _row, value = _pending_batches(db)
    tasks = value.get("users", {}).get(user.id, {}).get("tasks", {})
    return {"pending": len(tasks)}


@router.post("/tasks/notifications/flush")
async def flush_task_notifications(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    settings: Settings = Depends(get_settings),
) -> dict[str, int]:
    row, value = _pending_batches(db)
    users = value.setdefault("users", {})
    batch = users.get(user.id, {"tasks": {}})
    entries = list((batch.get("tasks") or {}).values())
    users.pop(user.id, None)
    row.value = value
    flag_modified(row, "value")
    if not entries or not _task_change_emails_enabled(db):
        db.commit()
        return {"sent": 0, "pending": 0}
    people_by_id = {person.id: person for person in db.scalars(select(Person).where(Person.active.is_(True))).all()}
    entries_by_recipient: dict[str, list[dict[str, Any]]] = {}
    for entry in entries:
        if not _change_lines(entry):
            continue
        for person_id in entry.get("recipient_ids", []):
            person = people_by_id.get(person_id)
            if person and person.email:
                entries_by_recipient.setdefault(person.id, []).append(entry)
    sent = 0
    for person_id, person_entries in entries_by_recipient.items():
        person = people_by_id[person_id]
        text_body, html_body = _render_task_digest(db, person, person_entries)
        await send_email_tool(
            ToolContext(
                db=db,
                actor_type="dashboard_user",
                actor_id=user.id,
                request_id=f"task-digest:{user.id}:{person.id}:{datetime.now(UTC).isoformat()}",
            ),
            settings,
            to_people=[person],
            subject="Task updates digest",
            text_body=text_body,
            html_body=html_body,
        )
        sent += 1
    db.commit()
    return {"sent": sent, "pending": 0}


@router.get("/projects", response_model=list[ProjectRead])
def projects(person_id: str | None = None, status: str | None = None, db: Session = Depends(get_db)) -> list[ProjectRead]:
    return [_project_read(db, project) for project in records.list_projects(db, person_id=person_id, status=status)]


@router.post("/projects", response_model=ProjectRead)
def create_project(
    payload: ProjectCreate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> ProjectRead:
    if db.get(Person, payload.person_id) is None:
        raise HTTPException(status_code=404, detail="Person not found")
    project = records.create_project(db, payload)
    write_audit(db, actor_type="dashboard_user", actor_id=user.id, action="create_project", entity_type="project", entity_id=project.id)
    db.commit()
    db.refresh(project)
    return _project_read(db, project)


@router.put("/projects/{project_id}", response_model=ProjectRead)
def update_project(
    project_id: str,
    payload: ProjectUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> ProjectRead:
    project = db.get(Project, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")
    if "person_id" in payload.model_fields_set and payload.person_id and db.get(Person, payload.person_id) is None:
        raise HTTPException(status_code=404, detail="Person not found")
    for field in payload.model_fields_set:
        setattr(project, field, getattr(payload, field))
    write_audit(db, actor_type="dashboard_user", actor_id=user.id, action="update_project", entity_type="project", entity_id=project.id)
    db.commit()
    db.refresh(project)
    return _project_read(db, project)


@router.get("/tasks", response_model=list[TaskRead])
def tasks(
    status: str | None = None,
    person_id: str | None = None,
    project_id: str | None = None,
    db: Session = Depends(get_db),
) -> list[TaskRead]:
    return [_task_read(db, task) for task in records.list_tasks(db, status=status, person_id=person_id, project_id=project_id)]


@router.post("/tasks", response_model=TaskRead)
async def create_task(
    payload: TaskCreate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    settings: Settings = Depends(get_settings),
) -> TaskRead:
    if payload.assigned_person_id and db.get(Person, payload.assigned_person_id) is None:
        raise HTTPException(status_code=404, detail="Person not found")
    if payload.project_id and db.get(Project, payload.project_id) is None:
        raise HTTPException(status_code=404, detail="Project not found")
    task = records.create_task(db, payload, created_by=user.id)
    desired_order = payload.priority_order or (1 if payload.project_id else None)
    _normalize_project_priority_sequence(db, task.project_id, focused_task=task, desired_order=desired_order)
    write_audit(db, actor_type="dashboard_user", actor_id=user.id, action="create_task", entity_type="task", entity_id=task.id)
    _queue_task_notification(db, user, task, action="created")
    db.commit()
    db.refresh(task)
    return _task_read(db, task)


def _task_notification_people(
    db: Session,
    task: Task,
    old_project_id: str | None = None,
    old_assigned_person_id: str | None = None,
) -> list[Person]:
    people_by_id: dict[str, Person] = {}
    for person_id in {task.assigned_person_id, old_assigned_person_id}:
        if not person_id:
            continue
        person = db.get(Person, person_id)
        if person is not None:
            people_by_id[person.id] = person
    for project_id in {task.project_id, old_project_id}:
        if not project_id:
            continue
        project = db.get(Project, project_id)
        project_person = db.get(Person, project.person_id) if project else None
        if project_person is not None:
            people_by_id[project_person.id] = project_person
    return list(people_by_id.values())


async def _send_task_change_email(
    db: Session,
    settings: Settings,
    user: User,
    task: Task,
    *,
    old_title: str | None = None,
    old_priority: str | None = None,
    old_project_id: str | None = None,
    old_assigned_person_id: str | None = None,
    old_status: str | None = None,
    old_due_date: datetime | None = None,
    old_priority_order: int | None = None,
) -> None:
    if not _task_change_emails_enabled(db):
        return
    people = _task_notification_people(db, task, old_project_id=old_project_id, old_assigned_person_id=old_assigned_person_id)
    changes: list[str] = []
    if old_title is not None and old_title != task.title:
        changes.append(f"Title changed from {old_title} to {task.title}.")
    if old_priority_order is not None and old_priority_order != task.priority_order:
        changes.append(f"Priority changed from {_project_priority_name(old_priority_order)} to {_project_priority_name(task.priority_order)}.")
    elif old_priority is not None and old_priority != task.priority:
        changes.append(f"Priority changed from {old_priority.title()} to {task.priority.title()}.")
    if old_project_id != task.project_id:
        old_project = db.get(Project, old_project_id) if old_project_id else None
        new_project = db.get(Project, task.project_id) if task.project_id else None
        old_name = old_project.name if old_project else "No project"
        new_name = new_project.name if new_project else "No project"
        changes.append(f"Project changed from {old_name} to {new_name}.")
    if old_assigned_person_id is not None and old_assigned_person_id != task.assigned_person_id:
        old_person = db.get(Person, old_assigned_person_id)
        new_person = db.get(Person, task.assigned_person_id) if task.assigned_person_id else None
        changes.append(
            f"Assignee changed from {old_person.full_name if old_person else 'none'} "
            f"to {new_person.full_name if new_person else 'none'}."
        )
    if old_status is not None and old_status != task.status:
        changes.append(f"Status changed from {old_status} to {task.status}.")
    if old_due_date is not None and old_due_date != task.due_date:
        old_due = old_due_date.date().isoformat()
        new_due = task.due_date.date().isoformat() if task.due_date else "none"
        changes.append(f"Due date changed from {old_due} to {new_due}.")
    if not people or not changes:
        return
    priority_list = _project_priority_list(db, task.project_id)
    body = f"The task \"{task.title}\" was updated.\n\n{_task_email_context(db, task)}\n\n" + "\n".join(changes)
    if priority_list:
        body += f"\n\n{priority_list}"
    subject = (
        f"Task priority changed: {task.title}"
        if len(changes) == 1 and changes[0].startswith("Priority changed")
        else f"Task updated: {task.title}"
    )
    await send_email_tool(
        ToolContext(
            db=db,
            actor_type="dashboard_user",
            actor_id=user.id,
            request_id=f"task-update:{task.id}:{datetime.now(UTC).isoformat()}",
        ),
        settings,
        to_people=people,
        subject=subject,
        text_body=body,
        related_task=task,
    )


async def _send_task_created_email(
    db: Session,
    settings: Settings,
    user: User,
    task: Task,
) -> None:
    if not _task_change_emails_enabled(db):
        return
    people = _task_notification_people(db, task)
    if not people:
        return
    lines = [f'The task "{task.title}" was created.', "", _task_email_context(db, task)]
    priority_list = _project_priority_list(db, task.project_id)
    if priority_list:
        lines.extend(["", priority_list])
    await send_email_tool(
        ToolContext(
            db=db,
            actor_type="dashboard_user",
            actor_id=user.id,
            request_id=f"task-create:{task.id}:{datetime.now(UTC).isoformat()}",
        ),
        settings,
        to_people=people,
        subject=f"Task created: {task.title}",
        text_body="\n".join(lines),
        related_task=task,
    )


@router.put("/tasks/{task_id}", response_model=TaskRead)
async def update_task(
    task_id: str,
    payload: TaskUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    settings: Settings = Depends(get_settings),
) -> TaskRead:
    task = db.get(Task, task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found")
    if (
        "assigned_person_id" in payload.model_fields_set
        and payload.assigned_person_id
        and db.get(Person, payload.assigned_person_id) is None
    ):
        raise HTTPException(status_code=404, detail="Person not found")
    if "project_id" in payload.model_fields_set and payload.project_id and db.get(Project, payload.project_id) is None:
        raise HTTPException(status_code=404, detail="Project not found")
    old_title = task.title
    old_priority = task.priority
    old_project_id = task.project_id
    old_assigned_person_id = task.assigned_person_id
    old_status = task.status
    old_due_date = task.due_date
    old_priority_order = task.priority_order
    before = _task_snapshot(db, task)
    project_priority_order = _project_priority_order(payload.priority) if payload.priority and (payload.project_id or task.project_id) else None
    for field in payload.model_fields_set:
        if field == "priority_order" and getattr(payload, field) is None:
            continue
        if field == "priority" and project_priority_order is not None:
            continue
        setattr(task, field, getattr(payload, field))
    requested_priority_order = payload.priority_order if "priority_order" in payload.model_fields_set else project_priority_order
    if old_project_id and old_project_id != task.project_id:
        _normalize_project_priority_sequence(db, old_project_id)
    if task.project_id and (old_project_id != task.project_id or requested_priority_order is not None or task.priority_order <= 0):
        _normalize_project_priority_sequence(db, task.project_id, focused_task=task, desired_order=requested_priority_order)
    if payload.status == "completed" and task.completed_at is None:
        task.completed_at = datetime.now(UTC)
    elif payload.status and payload.status != "completed":
        task.completed_at = None
    write_audit(
        db,
        actor_type="dashboard_user",
        actor_id=user.id,
        action="update_task",
        entity_type="task",
        entity_id=task.id,
        safe_metadata={"changed_fields": sorted(payload.model_fields_set)},
    )
    _queue_task_notification(
        db,
        user,
        task,
        action="updated",
        before=before,
        old_project_id=old_project_id,
        old_assigned_person_id=old_assigned_person_id if "assigned_person_id" in payload.model_fields_set else None,
    )
    db.commit()
    db.refresh(task)
    return _task_read(db, task)


@router.post("/tasks/{task_id}/priority", response_model=TaskRead)
async def update_task_priority(
    task_id: str,
    payload: TaskPriorityUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    settings: Settings = Depends(get_settings),
) -> TaskRead:
    task = db.get(Task, task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found")
    before = _task_snapshot(db, task)
    old_priority = task.priority
    old_priority_order = task.priority_order
    if task.project_id:
        task.priority_order = _project_priority_order(payload.priority) or task.priority_order
        _normalize_project_priority_sequence(db, task.project_id, focused_task=task, desired_order=task.priority_order)
    else:
        task.priority = payload.priority
        _normalize_project_priority_sequence(db, task.project_id)
    write_audit(
        db,
        actor_type="dashboard_user",
        actor_id=user.id,
        action="update_task_priority",
        entity_type="task",
        entity_id=task.id,
        safe_metadata={"old_priority": old_priority, "new_priority": payload.priority},
    )
    if old_priority != task.priority or old_priority_order != task.priority_order:
        _queue_task_notification(
            db,
            user,
            task,
            action="updated",
            before=before,
            old_project_id=task.project_id,
        )
    db.commit()
    db.refresh(task)
    return _task_read(db, task)


@router.post("/tasks/{task_id}/complete", response_model=TaskRead)
async def complete_task(
    task_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    settings: Settings = Depends(get_settings),
) -> TaskRead:
    existing = db.get(Task, task_id)
    old_status = existing.status if existing is not None else None
    old_project_id = existing.project_id if existing is not None else None
    before = _task_snapshot(db, existing) if existing is not None else None
    task = records.complete_task(db, task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found")
    write_audit(db, actor_type="dashboard_user", actor_id=user.id, action="complete_task", entity_type="task", entity_id=task.id)
    _queue_task_notification(db, user, task, action="updated", before=before, old_project_id=old_project_id)
    db.commit()
    db.refresh(task)
    return _task_read(db, task)


@router.get("/meetings", response_model=list[MeetingRead])
def meetings(db: Session = Depends(get_db)) -> list[MeetingRead]:
    return [MeetingRead.model_validate(meeting, from_attributes=True) for meeting in records.list_meetings(db)]


@router.post("/meetings", response_model=MeetingRead)
def create_meeting(
    payload: MeetingCreate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> MeetingRead:
    meeting = records.create_meeting(db, payload)
    write_audit(db, actor_type="dashboard_user", actor_id=user.id, action="create_meeting", entity_type="meeting", entity_id=meeting.id)
    db.commit()
    db.refresh(meeting)
    return MeetingRead.model_validate(meeting, from_attributes=True)


@router.get("/reminders", response_model=list[ReminderRead])
def reminders(db: Session = Depends(get_db)) -> list[ReminderRead]:
    return [ReminderRead.model_validate(reminder, from_attributes=True) for reminder in records.list_reminders(db)]


@router.post("/reminders", response_model=ReminderRead)
def create_reminder(
    payload: ReminderCreate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> ReminderRead:
    reminder = records.create_reminder(db, payload)
    write_audit(db, actor_type="dashboard_user", actor_id=user.id, action="create_reminder", entity_type="reminder", entity_id=reminder.id)
    db.commit()
    db.refresh(reminder)
    return ReminderRead.model_validate(reminder, from_attributes=True)
