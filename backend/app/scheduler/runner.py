import asyncio
import logging
import signal

from app.core.config import get_settings
from app.core.database import SessionLocal
from app.core.logging import configure_logging
from app.scheduler.jobs import due_jobs, execute_job

logger = logging.getLogger(__name__)


async def run_scheduler() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)
    logger.info("scheduler_started")
    while not stop.is_set():
        with SessionLocal() as db:
            for job in due_jobs(db):
                try:
                    execute_job(db, job)
                    db.commit()
                except Exception:
                    logger.exception("scheduler_job_failed", extra={"job_id": getattr(job, "id", None)})
                    db.rollback()
        try:
            await asyncio.wait_for(stop.wait(), timeout=10)
        except TimeoutError:
            pass
    logger.info("scheduler_stopped")


def main() -> None:
    asyncio.run(run_scheduler())


if __name__ == "__main__":
    main()
