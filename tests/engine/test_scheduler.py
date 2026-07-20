import threading
import time
from pathlib import Path

import pytest

from shouldertap.engine.clock import utcnow
from shouldertap.engine.contracts import ContextRequest
from shouldertap.engine.scheduler import core, jobs
from shouldertap.engine.store.engine import make_engine, make_session_factory
from shouldertap.engine.store.mappers import request_to_row
from shouldertap.engine.store.migrate import run_migrations
from shouldertap.engine.store.repository import get_timer


class RecordingContext:
    def __init__(self) -> None:
        self.escalations: list[str] = []
        self.give_ups: list[str] = []
        self.sweeps = 0
        self.event = threading.Event()

    def handle_escalation_timer(self, request_id: str) -> None:
        self.escalations.append(request_id)
        self.event.set()

    def handle_give_up_timer(self, request_id: str) -> None:
        self.give_ups.append(request_id)
        self.event.set()

    def run_quiet_hours_sweep(self) -> None:
        self.sweeps += 1
        self.event.set()


@pytest.fixture(autouse=True)
def _reset_job_context():
    yield
    jobs.set_context(None)  # type: ignore[arg-type]


def _make_request_row(session_factory, request_id: str) -> None:
    request = ContextRequest(
        id=request_id,
        kind="freeform.answer",
        topic="anything",
        question="q?",
        consumer="test-consumer",
    )
    with session_factory() as session:
        session.add(request_to_row(request))
        session.commit()


def test_schedule_escalation_creates_timer_row_and_apscheduler_job(tmp_path: Path) -> None:
    db_path = tmp_path / "shouldertap.db"
    run_migrations(db_path)
    session_factory = make_session_factory(make_engine(db_path))
    _make_request_row(session_factory, "req_1")

    scheduler = core.make_scheduler(db_path)
    fire_at = utcnow().replace(microsecond=0)

    with session_factory() as session:
        timer = core.schedule_escalation(scheduler, session, request_id="req_1", fire_at=fire_at)
        session.commit()
        timer_id = timer.id

    assert scheduler.get_job(timer_id) is not None

    with session_factory() as session:
        row = get_timer(session, timer_id)
        assert row is not None
        assert row.job_type == "escalation"
        assert row.status == "scheduled"
        assert row.apscheduler_job_id == timer_id


def test_overdue_job_fires_on_a_fresh_scheduler_after_restart(tmp_path: Path) -> None:
    """Simulates a crash: schedule a job on a *running* scheduler A (APScheduler only persists
    jobs to the jobstore once the scheduler is running -- jobs added beforehand just sit in an
    in-memory pending list), then abruptly shut it down before the job is due, so it never
    fires. A brand-new scheduler B, started later and pointed at the same DB file, must pick up
    the now-overdue job from the jobstore table and fire it (misfire_grace_time=None) -- this is
    the mechanism the crash/rehydration acceptance test (spec §15 criterion 6) exercises.
    """
    db_path = tmp_path / "shouldertap.db"
    run_migrations(db_path)
    session_factory = make_session_factory(make_engine(db_path))
    _make_request_row(session_factory, "req_2")

    scheduler_a = core.make_scheduler(db_path)
    scheduler_a.start()
    fire_at = utcnow().replace(microsecond=0) + __import__("datetime").timedelta(seconds=2)
    with session_factory() as session:
        core.schedule_escalation(scheduler_a, session, request_id="req_2", fire_at=fire_at)
        session.commit()
    # wait=True fully joins scheduler_a's background thread before we proceed -- otherwise it
    # can still be alive when scheduler_b starts and both race to process the same overdue job.
    # This still simulates a crash from the *process's* point of view: no job has fired
    # (fire_at is safely in the future), and scheduler_a is discarded rather than resumed.
    scheduler_a.shutdown(wait=True)

    time.sleep(2.5)  # let fire_at pass while nothing is running

    context = RecordingContext()
    jobs.set_context(context)
    scheduler_b = core.make_scheduler(db_path)
    scheduler_b.start()
    try:
        fired = context.event.wait(timeout=5)
        assert fired, "overdue escalation job did not fire after restart"
        assert context.escalations == ["req_2"]
    finally:
        scheduler_b.shutdown(wait=False)


def test_quiet_hours_sweep_runs_on_interval(tmp_path: Path) -> None:
    db_path = tmp_path / "shouldertap.db"
    run_migrations(db_path)

    context = RecordingContext()
    jobs.set_context(context)
    scheduler = core.make_scheduler(db_path)
    # Reach into the interval trigger via a direct add_job call at a near-immediate interval
    # for test speed, rather than waiting out the real 5-minute default.
    from apscheduler.triggers.interval import IntervalTrigger

    scheduler.add_job(
        jobs.run_quiet_hours_sweep,
        trigger=IntervalTrigger(seconds=1),
        id=core.QUIET_HOURS_SWEEP_JOB_ID,
        replace_existing=True,
    )
    scheduler.start()
    try:
        fired = context.event.wait(timeout=5)
        assert fired
        assert context.sweeps >= 1
    finally:
        scheduler.shutdown(wait=False)


def test_cancel_timer_removes_apscheduler_job_and_marks_row_cancelled(tmp_path: Path) -> None:
    db_path = tmp_path / "shouldertap.db"
    run_migrations(db_path)
    session_factory = make_session_factory(make_engine(db_path))
    _make_request_row(session_factory, "req_3")

    scheduler = core.make_scheduler(db_path)
    fire_at = utcnow().replace(microsecond=0) + __import__("datetime").timedelta(hours=2)

    with session_factory() as session:
        timer = core.schedule_give_up(scheduler, session, request_id="req_3", fire_at=fire_at)
        session.commit()
        timer_id = timer.id

    with session_factory() as session:
        core.cancel_timer(scheduler, session, timer_id)
        session.commit()

    assert scheduler.get_job(timer_id) is None
    with session_factory() as session:
        row = get_timer(session, timer_id)
        assert row is not None
        assert row.status == "cancelled"
