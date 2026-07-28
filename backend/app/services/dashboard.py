from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.entities import Email, Meeting, Message, Reminder, SchedulerJob, Task
from app.schemas.domain import DashboardSummary


def get_summary(db: Session) -> DashboardSummary:
    now = datetime.now(UTC)
    tomorrow = now + timedelta(days=1)
    today_meetings = db.scalar(
        select(func.count()).select_from(Meeting).where(Meeting.start_time >= now, Meeting.start_time < tomorrow)
    ) or 0
    upcoming_meetings = db.scalar(
        select(func.count()).select_from(Meeting).where(Meeting.start_time >= now, Meeting.status == "scheduled")
    ) or 0
    due_tasks = db.scalar(
        select(func.count()).select_from(Task).where(Task.due_date >= now, Task.due_date < tomorrow, Task.status != "completed")
    ) or 0
    overdue_tasks = db.scalar(select(func.count()).select_from(Task).where(Task.due_date < now, Task.status != "completed")) or 0
    pending_reminders = db.scalar(select(func.count()).select_from(Reminder).where(Reminder.status == "pending")) or 0
    recent_messages = db.scalar(select(func.count()).select_from(Message).where(Message.created_at >= now - timedelta(days=7))) or 0
    pending_email_approvals = db.scalar(
        select(func.count()).select_from(Email).where(Email.status.in_(["queued", "draft", "needs_confirmation"]))
    ) or 0
    failed_integrations = db.scalar(select(func.count()).select_from(Email).where(Email.status == "failed")) or 0
    failed_jobs = db.scalar(select(func.count()).select_from(SchedulerJob).where(SchedulerJob.status == "failed")) or 0
    return DashboardSummary(
        today_meetings=today_meetings,
        upcoming_meetings=upcoming_meetings,
        due_tasks=due_tasks,
        overdue_tasks=overdue_tasks,
        pending_reminders=pending_reminders,
        recent_messages=recent_messages,
        pending_email_approvals=pending_email_approvals,
        failed_integrations=failed_integrations,
        scheduler_health="attention" if failed_jobs else "ok",
    )
