import argparse
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, update

from app.auth.passwords import hash_password
from app.core.database import Base, SessionLocal, engine
from app.models.entities import (
    AppSetting,
    AuditLog,
    Conversation,
    Email,
    EmailRecipient,
    File,
    Meeting,
    MeetingNote,
    MeetingParticipant,
    Message,
    N8nRequest,
    Person,
    PersonGoal,
    Project,
    Reminder,
    ReplayGuard,
    SchedulerJob,
    Task,
    User,
)

NOTIFICATION_SETTINGS_KEY = "notification_settings"


def _ensure_admin() -> None:
    with SessionLocal() as db:
        if not db.query(User).filter(User.email == "admin@example.com").first():
            db.add(
                User(
                    name="Admin",
                    email="admin@example.com",
                    role="admin",
                    password_hash=hash_password("ChangeMeNow123!"),
                )
            )
            db.commit()
            print("Created admin@example.com / ChangeMeNow123!")
        else:
            print("Seed user already exists.")


def _clear_operational_data() -> None:
    with SessionLocal() as db:
        db.execute(update(Message).values(media_id=None))
        db.execute(update(File).values(related_message_id=None, related_person_id=None, related_meeting_id=None))
        for model in (
            SchedulerJob,
            ReplayGuard,
            EmailRecipient,
            Email,
            N8nRequest,
            Reminder,
            MeetingParticipant,
            MeetingNote,
            Meeting,
            Task,
            Project,
            PersonGoal,
            Message,
            Conversation,
            File,
            AuditLog,
            Person,
        ):
            db.execute(delete(model))
        db.merge(
            AppSetting(
                key=NOTIFICATION_SETTINGS_KEY,
                value={"task_change_email_notifications": True, "task_change_email_recipients": "related_people"},
            )
        )
        db.commit()
    print("Cleared operational people, projects, tasks, conversations, email logs, reminders, meetings, and audit logs.")


def _add_demo_data() -> None:
    now = datetime.now(UTC)
    with SessionLocal() as db:
        people = {
            "ali": Person(full_name="Ali Awdeh", company="Meet Tina", job_title="Operations Lead", email="ali.demo@meettina.net", active=True),
            "naji": Person(full_name="Naji Haddad", company="Meet Tina", job_title="Automation Owner", email="naji.demo@meettina.net", active=True),
            "youssef": Person(
                full_name="Youssef Darwish",
                company="Meet Tina",
                job_title="Growth Manager",
                email="youssef.demo@meettina.net",
                active=True,
            ),
            "lina": Person(full_name="Lina Karam", company="Meet Tina", job_title="Client Success", email="lina.demo@meettina.net", active=True),
        }
        db.add_all(people.values())
        db.flush()

        projects = {
            "gulfmates": Project(person_id=people["ali"].id, name="Gulfmates", description="Partner follow-up and launch tracking", status="active"),
            "travel": Project(person_id=people["ali"].id, name="Travel Assist", description="Travel support workflow", status="active"),
            "openwa": Project(person_id=people["naji"].id, name="OpenWA Reliability", description="WhatsApp automation checks", status="active"),
            "launch": Project(person_id=people["youssef"].id, name="Launch Ops", description="Launch execution list", status="active"),
            "portal": Project(person_id=people["lina"].id, name="Client Portal", description="Portal rollout tasks", status="active"),
        }
        db.add_all(projects.values())
        db.flush()

        db.add_all(
            [
                Task(
                    title="Confirm Gulfmates onboarding owner",
                    assigned_person_id=people["ali"].id,
                    project_id=projects["gulfmates"].id,
                    priority="urgent",
                    priority_order=1,
                    due_date=now + timedelta(days=1),
                ),
                Task(
                    title="Send Gulfmates contract summary",
                    assigned_person_id=people["ali"].id,
                    project_id=projects["gulfmates"].id,
                    priority="high",
                    priority_order=2,
                    due_date=now + timedelta(days=2),
                ),
                Task(
                    title="Draft Travel Assist user journey",
                    assigned_person_id=people["ali"].id,
                    project_id=projects["travel"].id,
                    priority="medium",
                    priority_order=1,
                    due_date=now + timedelta(days=4),
                ),
                Task(
                    title="Check OpenWA webhook replay handling",
                    assigned_person_id=people["naji"].id,
                    project_id=projects["openwa"].id,
                    priority="urgent",
                    priority_order=1,
                    due_date=now + timedelta(days=1),
                ),
                Task(
                    title="Document voice-note media flow",
                    assigned_person_id=people["naji"].id,
                    project_id=projects["openwa"].id,
                    priority="medium",
                    priority_order=2,
                    due_date=now + timedelta(days=3),
                ),
                Task(
                    title="Prepare launch checklist",
                    assigned_person_id=people["youssef"].id,
                    project_id=projects["launch"].id,
                    priority="high",
                    priority_order=1,
                    due_date=now + timedelta(days=2),
                ),
                Task(
                    title="Review portal onboarding copy",
                    assigned_person_id=people["lina"].id,
                    project_id=projects["portal"].id,
                    priority="medium",
                    priority_order=1,
                    due_date=now + timedelta(days=5),
                ),
                Task(
                    title="Call Sami with weekly risk summary",
                    assigned_person_id=people["lina"].id,
                    priority="low",
                    priority_order=0,
                    due_date=now + timedelta(days=6),
                ),
            ]
        )
        db.commit()
    print("Created demo people, projects, and numbered task priorities.")


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed Meet Tina local or server data.")
    parser.add_argument("--reset-demo", action="store_true", help="Remove operational data and create clean demo records.")
    args = parser.parse_args()

    Base.metadata.create_all(bind=engine)
    _ensure_admin()
    if args.reset_demo:
        _clear_operational_data()
        _add_demo_data()


if __name__ == "__main__":
    main()
