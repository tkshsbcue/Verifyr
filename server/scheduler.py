"""APScheduler integration: run enabled checks on their cron schedule."""

from __future__ import annotations

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from .db import SessionLocal
from .models import Check
from .runner import enqueue_run

scheduler = BackgroundScheduler(daemon=True)


def _job(check_id: int) -> None:
    db = SessionLocal()
    try:
        enqueue_run(db, check_id, trigger="scheduled")
    finally:
        db.close()


def sync_jobs() -> None:
    """Rebuild the job table from the DB. Call after any check create/update/delete."""
    if not scheduler.running:
        return
    scheduler.remove_all_jobs()
    db = SessionLocal()
    try:
        rows = db.query(Check).filter(Check.enabled.is_(True), Check.schedule.isnot(None)).all()
        for c in rows:
            if not c.schedule:
                continue
            try:
                scheduler.add_job(
                    _job, CronTrigger.from_crontab(c.schedule), args=[c.id], id=f"check-{c.id}",
                    replace_existing=True,
                )
            except (ValueError, TypeError):
                # Bad cron expression — skip rather than crash the scheduler.
                print(f"[scheduler] invalid cron for check {c.id}: {c.schedule!r}", flush=True)
    finally:
        db.close()


def start() -> None:
    if not scheduler.running:
        scheduler.start()
    sync_jobs()


def stop() -> None:
    if scheduler.running:
        scheduler.shutdown(wait=False)
