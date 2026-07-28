from datetime import UTC, datetime, timedelta

from app.core.database import SessionLocal
from app.models.entities import Meeting
from app.scheduler.jobs import schedule_meeting_preparation


def test_rescheduled_meeting_cancels_previous_job() -> None:
    with SessionLocal() as db:
        meeting = Meeting(title="Ops", start_time=datetime.now(UTC) + timedelta(hours=8), timezone="Asia/Beirut")
        db.add(meeting)
        db.flush()
        first = schedule_meeting_preparation(db, meeting)
        meeting.start_time = meeting.start_time + timedelta(hours=2)
        second = schedule_meeting_preparation(db, meeting)
        db.commit()
        assert first is not None
        assert second is not None
        assert first.status == "cancelled"
        assert second.status == "scheduled"
