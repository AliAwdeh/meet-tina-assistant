from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.entities import Meeting, MeetingParticipant, Person, Project, Reminder, Task
from app.schemas.domain import MeetingCreate, PersonCreate, ProjectCreate, ReminderCreate, TaskCreate


def search_people(db: Session, query: str | None = None, limit: int = 50) -> list[Person]:
    stmt = select(Person).where(Person.active.is_(True)).order_by(Person.full_name).limit(limit)
    if query:
        stmt = stmt.where(Person.full_name.ilike(f"%{query}%"))
    return list(db.scalars(stmt))


def create_person(db: Session, payload: PersonCreate) -> Person:
    person = Person(**payload.model_dump())
    db.add(person)
    db.flush()
    return person


def list_projects(db: Session, person_id: str | None = None, status: str | None = None, limit: int = 100) -> list[Project]:
    stmt = select(Project).order_by(Project.created_at.desc()).limit(limit)
    if person_id:
        stmt = stmt.where(Project.person_id == person_id)
    if status:
        stmt = stmt.where(Project.status == status)
    return list(db.scalars(stmt))


def create_project(db: Session, payload: ProjectCreate) -> Project:
    project = Project(**payload.model_dump())
    db.add(project)
    db.flush()
    return project


def list_tasks(
    db: Session,
    status: str | None = None,
    person_id: str | None = None,
    project_id: str | None = None,
    limit: int = 100,
) -> list[Task]:
    stmt = select(Task).order_by(Task.priority_order.asc(), Task.due_date.asc().nullslast(), Task.created_at.desc()).limit(limit)
    if status:
        stmt = stmt.where(Task.status == status)
    if person_id:
        stmt = stmt.where(Task.assigned_person_id == person_id)
    if project_id:
        stmt = stmt.where(Task.project_id == project_id)
    return list(db.scalars(stmt))


def create_task(db: Session, payload: TaskCreate, created_by: str | None = None) -> Task:
    data = payload.model_dump()
    if data.get("priority_order") is None:
        data.pop("priority_order")
    task = Task(**data, created_by=created_by)
    db.add(task)
    db.flush()
    return task


def complete_task(db: Session, task_id: str) -> Task | None:
    task = db.get(Task, task_id)
    if task is None:
        return None
    task.status = "completed"
    task.completed_at = datetime.now(UTC)
    db.flush()
    return task


def list_meetings(db: Session, limit: int = 100) -> list[Meeting]:
    return list(db.scalars(select(Meeting).order_by(Meeting.start_time).limit(limit)))


def create_meeting(db: Session, payload: MeetingCreate) -> Meeting:
    data = payload.model_dump(exclude={"participant_ids"})
    meeting = Meeting(**data)
    db.add(meeting)
    db.flush()
    for person_id in payload.participant_ids:
        db.add(MeetingParticipant(meeting_id=meeting.id, person_id=person_id))
    db.flush()
    return meeting


def list_reminders(db: Session, limit: int = 100) -> list[Reminder]:
    return list(db.scalars(select(Reminder).order_by(Reminder.trigger_time).limit(limit)))


def create_reminder(db: Session, payload: ReminderCreate) -> Reminder:
    reminder = Reminder(**payload.model_dump())
    db.add(reminder)
    db.flush()
    return reminder
