import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.entities import Meeting, SchedulerJob
from app.services.audit import write_audit

logger = logging.getLogger(__name__)


def schedule_meeting_preparation(db: Session, meeting: Meeting) -> SchedulerJob | None:
    if meeting.status != "scheduled":
        return None
    run_at = meeting.start_time - timedelta(hours=meeting.preparation_offset_hours)
    if run_at < datetime.now(UTC):
        run_at = datetime.now(UTC)
    existing = db.scalar(
        select(SchedulerJob).where(
            SchedulerJob.job_type == "meeting_preparation",
            SchedulerJob.entity_type == "meeting",
            SchedulerJob.entity_id == meeting.id,
            SchedulerJob.status == "scheduled",
        )
    )
    if existing:
        existing.status = "cancelled"
    job = SchedulerJob(
        job_type="meeting_preparation",
        entity_type="meeting",
        entity_id=meeting.id,
        run_at=run_at,
        idempotency_key=f"meeting_preparation:{meeting.id}:{meeting.start_time.isoformat()}",
        payload={"meeting_id": meeting.id},
    )
    db.add(job)
    db.flush()
    write_audit(db, actor_type="scheduler", action="schedule_meeting_preparation", entity_type="meeting", entity_id=meeting.id)
    return job


def cancel_meeting_jobs(db: Session, meeting_id: str) -> int:
    jobs = list(
        db.scalars(
            select(SchedulerJob).where(
                SchedulerJob.entity_type == "meeting",
                SchedulerJob.entity_id == meeting_id,
                SchedulerJob.status == "scheduled",
            )
        )
    )
    for job in jobs:
        job.status = "cancelled"
    return len(jobs)


def due_jobs(db: Session, limit: int = 10) -> list[SchedulerJob]:
    return list(
        db.scalars(
            select(SchedulerJob)
            .where(SchedulerJob.status == "scheduled", SchedulerJob.run_at <= datetime.now(UTC))
            .order_by(SchedulerJob.run_at)
            .limit(limit)
        )
    )


def execute_job(db: Session, job: SchedulerJob) -> None:
    job.attempts += 1
    job.locked_at = datetime.now(UTC)
    if job.job_type == "meeting_preparation":
        meeting = db.get(Meeting, job.entity_id)
        if meeting is None or meeting.status != "scheduled":
            job.status = "cancelled"
            return
        meeting.preparation_status = "completed"
        job.status = "completed"
        write_audit(db, actor_type="scheduler", action="complete_meeting_preparation", entity_type="meeting", entity_id=meeting.id)
        return
    job.status = "failed"
    write_audit(db, actor_type="scheduler", action="unknown_job", entity_type="scheduler_job", entity_id=job.id)
