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
    return sorted(
        tasks,
        key=lambda task: (
            task.priority_order if task.priority_order > 0 else 1_000_000,
            task.created_at.timestamp() if task.created_at else 0,
        ),
    )


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
    tasks = [task for task in _ordered_project_tasks(db, project_id) if task.priority_order > 0]
    if focused_task is not None and desired_order is not None:
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
    tasks = [task for task in _ordered_project_tasks(db, project_id) if task.priority_order > 0]
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
        "project_status": project.status if project else None,
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


def _queue_task_deletion_notification(db: Session, user: User, task: Task) -> None:
    if not _task_change_emails_enabled(db):
        return
    people = _task_notification_people(db, task)
    if not any(person.email for person in people):
        return
    before = _task_snapshot(db, task)
    row, value = _pending_batches(db)
    users = value.setdefault("users", {})
    batch = users.setdefault(user.id, {"tasks": {}})
    tasks = batch.setdefault("tasks", {})
    tasks[task.id] = {
        "action": "deleted",
        "before": before,
        "after": before,
        "recipient_ids": sorted({person.id for person in people}),
        "project_ids": sorted({project_id for project_id in [task.project_id] if project_id}),
        "created_at": datetime.now(UTC).isoformat(),
    }
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
    if action == "deleted":
        return [
            "Deleted task.",
            f"Was assigned to {before.get('assigned_person_name', 'Unassigned')}.",
            f"Was in project: {before.get('project_name', 'No project')}.",
            f"Priority was {_snapshot_priority(before)}.",
        ]
    lines: list[str] = []
    if before.get("title") != after.get("title"):
        lines.append(f"Title changed from {before.get('title')} to {after.get('title')}.")
    if before.get("description") != after.get("description"):
        lines.append("Description updated.")
    if before.get("project_id") != after.get("project_id"):
        lines.append(f"Project changed from {before.get('project_name', 'No project')} to {after.get('project_name', 'No project')}.")
    elif before.get("project_name") != after.get("project_name"):
        lines.append(f"Project renamed from {before.get('project_name', 'No project')} to {after.get('project_name', 'No project')}.")
    if before.get("project_status") != after.get("project_status"):
        lines.append(f"Project status changed from {before.get('project_status') or 'none'} to {after.get('project_status') or 'none'}.")
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
    for task in [task for task in _ordered_project_tasks(db, project_id) if task.priority_order > 0]:
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


def _unique_labels(values: list[str]) -> list[str]:
    seen: set[str] = set()
    labels: list[str] = []
    for value in values:
        normalized = value.strip()
        key = normalized.lower()
        if normalized and key not in seen:
            seen.add(key)
            labels.append(normalized)
    return labels


def _clip_subject(value: str, limit: int = 140) -> str:
    if len(value) <= limit:
        return value
    return value[: limit - 3].rstrip() + "..."


def _task_digest_subject(recipient: Person, entries: list[dict[str, Any]]) -> str:
    changed_entries = [(entry, _change_lines(entry)) for entry in entries]
    changed_entries = [(entry, lines) for entry, lines in changed_entries if lines]
    if not changed_entries:
        return f"Task updates for {recipient.full_name}"
    if len(changed_entries) == 1:
        entry, lines = changed_entries[0]
        after = entry.get("after") or {}
        title = str(after.get("title") or "task")
        if entry.get("action") == "created":
            return _clip_subject(f"Task created: {title}")
        if entry.get("action") == "deleted":
            return _clip_subject(f"Task deleted: {title}")
        changed_labels: list[str] = []
        if any(line.startswith("Priority changed") for line in lines):
            changed_labels.append("priority")
        if any(line.startswith("Project changed") for line in lines):
            changed_labels.append("project")
        if any(line.startswith("Assignee changed") for line in lines):
            changed_labels.append("assignee")
        if any(line.startswith("Status changed") for line in lines):
            changed_labels.append("status")
        if any(line.startswith("Title changed") for line in lines):
            changed_labels.append("title")
        if len(changed_labels) > 1:
            label_text = ", ".join(changed_labels[:-1]) + f" and {changed_labels[-1]}"
            return _clip_subject(f"Task {label_text} changed: {title}")
        if changed_labels == ["priority"]:
            return _clip_subject(f"Task priority changed: {title}")
        if changed_labels == ["project"]:
            return _clip_subject(f"Task moved: {title}")
        if changed_labels == ["assignee"]:
            return _clip_subject(f"Task reassigned: {title}")
        if changed_labels == ["status"]:
            return _clip_subject(f"Task status changed: {title}")
        if changed_labels == ["title"]:
            return _clip_subject(f"Task renamed: {title}")
        return _clip_subject(f"Task updated: {title}")
    created_count = sum(1 for entry, _lines in changed_entries if entry.get("action") == "created")
    updated_count = len(changed_entries) - created_count
    project_names = _unique_labels(
        [str((entry.get("after") or {}).get("project_name") or "No project") for entry, _lines in changed_entries]
    )
    summary_parts: list[str] = []
    if created_count:
        summary_parts.append(f"{created_count} created")
    if updated_count:
        summary_parts.append(f"{updated_count} updated")
    if len(project_names) == 1:
        scope = f"for {project_names[0]}"
    elif len(project_names) > 1:
        scope = f"across {len(project_names)} projects"
    else:
        scope = f"for {recipient.full_name}"
    return _clip_subject(f"Task updates: {', '.join(summary_parts)} {scope}")


def _render_task_digest(db: Session, recipient: Person, entries: list[dict[str, Any]], subject: str) -> tuple[str, str]:
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
    update_count = len(changed_entries)
    project_count = len(project_ids)
    text_lines = [f"Hi {recipient.full_name},", "", subject, "", "Here are the latest task updates:", ""]
    html_parts = [
        "<div style=\"display:none;max-height:0;overflow:hidden;color:transparent;opacity:0;\">"
        f"{escape(subject)}"
        "</div>",
        "<div style=\"font-family:Inter,Arial,sans-serif;background:#f6f2eb;padding:28px;color:#1f2a24;\">",
        "<div style=\"max-width:760px;margin:0 auto;background:#ffffff;border:1px solid #e6ded2;"
        "border-radius:14px;overflow:hidden;box-shadow:0 10px 30px rgba(31,42,36,.08);\">",
        "<div style=\"background:#20352b;padding:24px 26px;color:#ffffff;\">",
        "<p style=\"margin:0 0 8px;color:#9ad1af;font-size:12px;font-weight:800;"
        "letter-spacing:.08em;text-transform:uppercase;\">babysitting</p>",
        f"<h1 style=\"margin:0;font-size:24px;line-height:1.25;font-weight:800;\">{escape(subject)}</h1>",
        f"<p style=\"margin:10px 0 0;color:#dbe7dd;font-size:14px;line-height:1.55;\">"
        f"Hi {escape(recipient.full_name)}, here is the clean summary of what changed.</p>",
        "<div style=\"margin-top:18px;display:flex;gap:10px;flex-wrap:wrap;\">",
        f"<span style=\"display:inline-block;background:#ffffff;color:#20352b;border-radius:999px;"
        f"padding:7px 12px;font-size:13px;font-weight:800;\">{update_count} task update"
        f"{'s' if update_count != 1 else ''}</span>",
        f"<span style=\"display:inline-block;background:#9ad1af;color:#20352b;border-radius:999px;"
        f"padding:7px 12px;font-size:13px;font-weight:800;\">{project_count} priority list"
        f"{'s' if project_count != 1 else ''}</span>",
        "</div>",
        "</div>",
        "<div style=\"padding:6px 26px 26px;\">",
    ]
    by_project: dict[str, list[tuple[dict[str, Any], list[str]]]] = {}
    for entry, lines in changed_entries:
        after = entry.get("after") or {}
        by_project.setdefault(str(after.get("project_name") or "No project"), []).append((entry, lines))
    for project_name, project_entries in by_project.items():
        text_lines.append(f"{project_name}")
        html_parts.append(
            f"<section style=\"margin-top:20px;border:1px solid #e8dfd2;border-radius:12px;overflow:hidden;background:#fffdf9;\">"
            f"<div style=\"background:#f3eadf;padding:12px 16px;font-weight:800;color:#20352b;\">{escape(project_name)}</div>"
            f"<div style=\"padding:14px 16px;\">"
        )
        for entry, lines in project_entries:
            after = entry.get("after") or {}
            text_lines.append(f"- {after.get('title')}")
            task_title = escape(str(after.get("title") or "Untitled task"))
            html_parts.append(
                f"<div style=\"border-bottom:1px solid #eee7dd;padding:12px 0;\">"
                f"<div style=\"font-weight:800;margin-bottom:8px;color:#1f2a24;\">{task_title}</div>"
                f"<ul style=\"margin:0;padding-left:18px;color:#4b443d;font-size:14px;line-height:1.7;\">"
            )
            for line in lines:
                text_lines.append(f"  - {line}")
                html_parts.append(f"<li>{escape(line)}</li>")
            html_parts.append("</ul></div>")
        html_parts.append("</div></section>")
        text_lines.append("")
    project_priority_groups = [(project_id, _project_priority_items(db, project_id)) for project_id in project_ids]
    project_priority_groups = [(project_id, items) for project_id, items in project_priority_groups if items]
    if project_priority_groups:
        text_lines.append("Current project priority lists:")
        html_parts.append("<h2 style=\"margin:28px 0 10px;font-size:18px;color:#20352b;\">Current project priority lists</h2>")
        for _project_id, items in project_priority_groups:
            project_name = items[0]["project"]
            text_lines.append(project_name)
            html_parts.append(
                f"<section style=\"margin-top:12px;border:1px solid #d6e8dc;border-radius:12px;background:#fbfdfb;padding:16px;\">"
                f"<div style=\"font-weight:800;margin-bottom:12px;color:#2f6b46;\">{escape(project_name)}</div>"
            )
            for item in items:
                text_lines.append(f"- {item['priority']}: {item['title']} ({item['assignee']})")
                html_parts.append(
                    f"<div style=\"padding:9px 0;border-top:1px solid #edf4ee;\">"
                    f"<span style=\"display:inline-block;min-width:72px;font-weight:800;color:#20352b;\">{escape(item['priority'])}</span>"
                    f"<span style=\"color:#1f2a24;\">{escape(item['title'])}</span>"
                    f"<span style=\"color:#7a7168;\"> ({escape(item['assignee'])})</span>"
                    f"</div>"
                )
            html_parts.append("</section>")
            text_lines.append("")
    html_parts.extend(
        [
            "<p style=\"margin:24px 0 0;color:#7a7168;font-size:12px;line-height:1.5;\">"
            "This email was generated from the latest saved task changes in babysitting.</p>",
            "</div>",
            "</div>",
            "</div>",
        ]
    )
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
        subject = _task_digest_subject(person, person_entries)
        text_body, html_body = _render_task_digest(db, person, person_entries, subject)
        await send_email_tool(
            ToolContext(
                db=db,
                actor_type="dashboard_user",
                actor_id=user.id,
                request_id=f"task-digest:{user.id}:{person.id}:{datetime.now(UTC).isoformat()}",
            ),
            settings,
            to_people=[person],
            subject=subject,
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
    affected_tasks = list(db.scalars(select(Task).where(Task.project_id == project.id, Task.status.not_in(["completed", "cancelled"]))))
    task_snapshots = {task.id: _task_snapshot(db, task) for task in affected_tasks}
    for field in payload.model_fields_set:
        setattr(project, field, getattr(payload, field))
    db.flush()
    for task in affected_tasks:
        _queue_task_notification(db, user, task, action="updated", before=task_snapshots[task.id], old_project_id=project.id)
    write_audit(db, actor_type="dashboard_user", actor_id=user.id, action="update_project", entity_type="project", entity_id=project.id)
    db.commit()
    db.refresh(project)
    return _project_read(db, project)


@router.delete("/projects/{project_id}")
def delete_project(
    project_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict[str, int | bool]:
    project = db.get(Project, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")
    affected_tasks = list(db.scalars(select(Task).where(Task.project_id == project.id, Task.status.not_in(["completed", "cancelled"]))))
    snapshots = {task.id: _task_snapshot(db, task) for task in affected_tasks}
    for task in affected_tasks:
        task.project_id = None
        task.priority_order = 0
        _queue_task_notification(db, user, task, action="updated", before=snapshots[task.id], old_project_id=project.id)
    write_audit(
        db,
        actor_type="dashboard_user",
        actor_id=user.id,
        action="delete_project",
        entity_type="project",
        entity_id=project.id,
        safe_metadata={"detached_task_count": len(affected_tasks), "project_name": project.name},
    )
    db.delete(project)
    db.commit()
    return {"deleted": True, "detached_tasks": len(affected_tasks)}


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
    desired_order = payload.priority_order
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
        changes.append(
            f"Priority changed from {_project_priority_name(old_priority_order)} "
            f"to {_project_priority_name(task.priority_order)}."
        )
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
    old_project_id = task.project_id
    old_assigned_person_id = task.assigned_person_id
    before = _task_snapshot(db, task)
    project_priority_order = (
        _project_priority_order(payload.priority)
        if payload.priority and (payload.project_id or task.project_id)
        else None
    )
    for field in payload.model_fields_set:
        if field == "priority_order" and getattr(payload, field) is None:
            continue
        if field == "priority" and project_priority_order is not None:
            continue
        setattr(task, field, getattr(payload, field))
    requested_priority_order = payload.priority_order if "priority_order" in payload.model_fields_set else project_priority_order
    if old_project_id and old_project_id != task.project_id:
        _normalize_project_priority_sequence(db, old_project_id)
    if task.project_id and (old_project_id != task.project_id or requested_priority_order is not None):
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


@router.delete("/tasks/{task_id}")
def delete_task(
    task_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict[str, bool]:
    task = db.get(Task, task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found")
    old_project_id = task.project_id
    _queue_task_deletion_notification(db, user, task)
    write_audit(db, actor_type="dashboard_user", actor_id=user.id, action="delete_task", entity_type="task", entity_id=task.id)
    db.delete(task)
    db.flush()
    _normalize_project_priority_sequence(db, old_project_id)
    db.commit()
    return {"deleted": True}


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
