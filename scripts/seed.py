import argparse

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
from sqlalchemy import delete, update

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
    team = [
        ("Ali Awdeh", "Ali.Awdeh@maids.cc", ["TA Bot", "PRO Bot", "Collect Info Bot", "Chatbots CX"]),
        ("Mohamad Darwish", "mohammad.darwish@maids.cc", ["Gulf Maids"]),
        ("Georgio Elias", "georgio@maids.cc", ["MMR"]),
        ("Jad Baraket", "jadbarakat@maids.cc", []),
        ("Karim el moghraby", "mugy@maids.cc", ["Government Affairs"]),
        ("Karla ElBanna", "karla@maids.cc", ["Risk"]),
        ("Marilyn Gharios", "marilyn.gharios@maids.cc", ["MV Maids Retention"]),
        ("Mohannad Akoum", "mohannad.akoum@maids.cc", ["MV CX"]),
        ("Peter mansour", "peter.mansour@maids.cc", ["PRO SERVICES", "Travel Assist"]),
        ("Razane Arnaout", "razane.arnaout@maids.cc", ["MV Resolvers"]),
        ("Tebarek Abdulkader", "tebarek.abdulkadir@maids.cc", []),
        ("Yousif Abu Taam", "yousif.abutaam@maids.cc", ["Maids.at", "Bookers"]),
    ]
    with SessionLocal() as db:
        for full_name, email, project_names in team:
            person = Person(full_name=full_name, company="Maids.cc", email=email, active=True)
            db.add(person)
            db.flush()
            for project_name in project_names:
                db.add(Project(person_id=person.id, name=project_name, status="active"))
        db.commit()
    print("Created Maids.cc people and projects with no tasks.")


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed babysitting local or server data.")
    parser.add_argument("--reset-demo", action="store_true", help="Remove operational data and create clean demo records.")
    args = parser.parse_args()

    Base.metadata.create_all(bind=engine)
    _ensure_admin()
    if args.reset_demo:
        _clear_operational_data()
        _add_demo_data()


if __name__ == "__main__":
    main()
