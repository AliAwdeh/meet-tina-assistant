from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.agent.tools.registry import ToolContext, send_email_tool
from app.auth.dependencies import get_current_user
from app.core.config import Settings, get_settings
from app.core.database import get_db
from app.models.entities import Person, Project, Task, User
from app.repositories import records
from app.schemas.domain import (
    DashboardSummary,
    MeetingCreate,
    MeetingRead,
    PersonCreate,
    PersonRead,
    ProjectCreate,
    ProjectRead,
    ReminderCreate,
    ReminderRead,
    TaskCreate,
    TaskPriorityUpdate,
    TaskRead,
)
from app.services import dashboard as dashboard_service
from app.services.audit import write_audit

router = APIRouter(dependencies=[Depends(get_current_user)])


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


@router.get("/tasks", response_model=list[TaskRead])
def tasks(
    status: str | None = None,
    person_id: str | None = None,
    project_id: str | None = None,
    db: Session = Depends(get_db),
) -> list[TaskRead]:
    return [_task_read(db, task) for task in records.list_tasks(db, status=status, person_id=person_id, project_id=project_id)]


@router.post("/tasks", response_model=TaskRead)
def create_task(
    payload: TaskCreate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> TaskRead:
    if payload.assigned_person_id and db.get(Person, payload.assigned_person_id) is None:
        raise HTTPException(status_code=404, detail="Person not found")
    if payload.project_id and db.get(Project, payload.project_id) is None:
        raise HTTPException(status_code=404, detail="Project not found")
    task = records.create_task(db, payload, created_by=user.id)
    write_audit(db, actor_type="dashboard_user", actor_id=user.id, action="create_task", entity_type="task", entity_id=task.id)
    db.commit()
    db.refresh(task)
    return _task_read(db, task)


async def _send_priority_change_email(db: Session, settings: Settings, user: User, task: Task, old_priority: str) -> None:
    people: list[Person] = []
    if task.assigned_person_id:
        person = db.get(Person, task.assigned_person_id)
        if person is not None:
            people.append(person)
    if not people and task.project_id:
        project = db.get(Project, task.project_id)
        project_person = db.get(Person, project.person_id) if project else None
        if project_person is not None:
            people.append(project_person)
    if not people:
        return
    project = db.get(Project, task.project_id) if task.project_id else None
    subject = f"Task priority changed: {task.title}"
    text_body = (
        f"The priority for task \"{task.title}\" changed from {old_priority} to {task.priority}."
        + (f"\n\nProject: {project.name}" if project else "")
    )
    await send_email_tool(
        ToolContext(
            db=db,
            actor_type="dashboard_user",
            actor_id=user.id,
            request_id=f"priority-change:{task.id}:{task.updated_at.isoformat()}",
        ),
        settings,
        to_people=people,
        subject=subject,
        text_body=text_body,
        related_task=task,
    )


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
    old_priority = task.priority
    task.priority = payload.priority
    write_audit(
        db,
        actor_type="dashboard_user",
        actor_id=user.id,
        action="update_task_priority",
        entity_type="task",
        entity_id=task.id,
        safe_metadata={"old_priority": old_priority, "new_priority": payload.priority},
    )
    if old_priority != payload.priority:
        await _send_priority_change_email(db, settings, user, task, old_priority)
    db.commit()
    db.refresh(task)
    return _task_read(db, task)


@router.post("/tasks/{task_id}/complete", response_model=TaskRead)
def complete_task(
    task_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> TaskRead:
    task = records.complete_task(db, task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found")
    write_audit(db, actor_type="dashboard_user", actor_id=user.id, action="complete_task", entity_type="task", entity_id=task.id)
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
