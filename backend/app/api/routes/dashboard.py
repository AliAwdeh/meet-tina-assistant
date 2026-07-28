from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.auth.dependencies import get_current_user
from app.core.database import get_db
from app.models.entities import User
from app.repositories import records
from app.schemas.domain import (
    DashboardSummary,
    MeetingCreate,
    MeetingRead,
    PersonCreate,
    PersonRead,
    ReminderCreate,
    ReminderRead,
    TaskCreate,
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


@router.get("/tasks", response_model=list[TaskRead])
def tasks(status: str | None = None, db: Session = Depends(get_db)) -> list[TaskRead]:
    return [TaskRead.model_validate(task, from_attributes=True) for task in records.list_tasks(db, status)]


@router.post("/tasks", response_model=TaskRead)
def create_task(
    payload: TaskCreate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> TaskRead:
    task = records.create_task(db, payload, created_by=user.id)
    write_audit(db, actor_type="dashboard_user", actor_id=user.id, action="create_task", entity_type="task", entity_id=task.id)
    db.commit()
    db.refresh(task)
    return TaskRead.model_validate(task, from_attributes=True)


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
    return TaskRead.model_validate(task, from_attributes=True)


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
