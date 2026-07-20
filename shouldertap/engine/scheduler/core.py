"""APScheduler wired to the same SQLite file as the app's own engine, via SQLAlchemyJobStore --
the jobstore auto-creates its own table and handles restart rehydration, provided job functions
are stable dotted paths (see jobs.py). `misfire_grace_time=None` means a job that came due while
the process was down still fires on restart rather than being silently dropped (needed for the
crash/rehydration acceptance test).
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from apscheduler.jobstores.base import JobLookupError
from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore
from apscheduler.schedulers.background import BackgroundScheduler
from sqlalchemy.orm import Session

from shouldertap.engine.scheduler import jobs
from shouldertap.engine.store.engine import make_engine
from shouldertap.engine.store.models import TimerRow
from shouldertap.engine.store.repository import create_timer, update_timer_status

QUIET_HOURS_SWEEP_JOB_ID = "quiet_hours_sweep"
QUIET_HOURS_SWEEP_INTERVAL_MINUTES = 5


def make_scheduler(db_path: Path) -> BackgroundScheduler:
    """timezone=UTC is required, not cosmetic: engine datetimes are naive UTC throughout (see
    clock.py), and APScheduler otherwise defaults to the local system timezone -- without this,
    a naive-UTC `fire_at` gets silently reinterpreted as naive-local, shifting every timer by
    the local UTC offset.

    The jobstore is given its own SQLAlchemy engine, built via the same make_engine() the app's
    request-handling code uses, so it picks up the same WAL-mode/busy-timeout pragmas --
    otherwise this second, independently-created connection to the same SQLite file can hit
    "database is locked" under concurrent access.
    """
    jobstore = SQLAlchemyJobStore(engine=make_engine(db_path))
    return BackgroundScheduler(
        timezone=UTC,
        jobstores={"default": jobstore},
        job_defaults={"misfire_grace_time": None, "coalesce": True, "max_instances": 1},
    )


def schedule_escalation(
    scheduler: BackgroundScheduler, session: Session, *, request_id: str, fire_at: datetime
) -> TimerRow:
    timer = create_timer(session, request_id=request_id, job_type="escalation", fire_at=fire_at)
    # Commit before touching the scheduler: add_job() writes through APScheduler's own,
    # independent SQLite connection to the same file. If our session still has this timer
    # insert (or anything else) pending in an open transaction, that connection holds the
    # write lock add_job() needs -- and since we're the ones blocking on add_job()'s return,
    # nothing would ever release it. See engine.store.engine.configure_sqlite_engine.
    session.commit()
    scheduler.add_job(
        jobs.fire_escalation,
        trigger="date",
        run_date=fire_at,
        args=[request_id],
        id=timer.id,
        replace_existing=True,
    )
    timer.apscheduler_job_id = timer.id
    return timer


def schedule_give_up(
    scheduler: BackgroundScheduler, session: Session, *, request_id: str, fire_at: datetime
) -> TimerRow:
    timer = create_timer(session, request_id=request_id, job_type="give_up", fire_at=fire_at)
    session.commit()  # see schedule_escalation
    scheduler.add_job(
        jobs.fire_give_up,
        trigger="date",
        run_date=fire_at,
        args=[request_id],
        id=timer.id,
        replace_existing=True,
    )
    timer.apscheduler_job_id = timer.id
    return timer


def ensure_quiet_hours_sweep(scheduler: BackgroundScheduler) -> None:
    """One recurring sweep job, not a one-off per queued ask -- see build plan's "Quiet-hours
    flush is one recurring sweep job" decision.
    """
    scheduler.add_job(
        jobs.run_quiet_hours_sweep,
        trigger="interval",
        minutes=QUIET_HOURS_SWEEP_INTERVAL_MINUTES,
        id=QUIET_HOURS_SWEEP_JOB_ID,
        replace_existing=True,
    )


def cancel_timer(scheduler: BackgroundScheduler, session: Session, timer_id: str) -> None:
    session.commit()  # see schedule_escalation -- remove_job() needs the write lock too
    try:
        scheduler.remove_job(timer_id)
    except JobLookupError:
        pass
    update_timer_status(session, timer_id, "cancelled")
