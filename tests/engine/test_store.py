from datetime import timedelta
from pathlib import Path

from shouldertap.engine.clock import utcnow
from shouldertap.engine.contracts import ContextRequest
from shouldertap.engine.store import repository as repo
from shouldertap.engine.store.engine import make_engine, make_session_factory
from shouldertap.engine.store.mappers import request_to_row, row_to_request
from shouldertap.engine.store.migrate import run_migrations


def _session_factory(tmp_path: Path):
    db_path = tmp_path / "shouldertap.db"
    run_migrations(db_path)
    engine = make_engine(db_path)
    return make_session_factory(engine)


def test_migration_creates_all_six_tables(tmp_path: Path) -> None:
    import sqlite3

    db_path = tmp_path / "shouldertap.db"
    run_migrations(db_path)
    con = sqlite3.connect(db_path)
    tables = {r[0] for r in con.execute("select name from sqlite_master where type='table'")}
    assert {"requests", "proposals", "experts", "consumers", "audit_events", "timers"} <= tables


def test_request_round_trips_through_store(tmp_path: Path) -> None:
    session_factory = _session_factory(tmp_path)
    request = ContextRequest(
        kind="glossary.definition",
        topic="revenue metrics",
        question="What does active customer mean?",
        consumer="bi.assistant",
        dedup_key="glossary:dim_customers.active_flag",
    )

    with session_factory() as session:
        session.add(request_to_row(request))
        session.commit()

    with session_factory() as session:
        row = repo.get_request(session, request.id)
        assert row is not None
        back = row_to_request(row)
        assert back.id == request.id
        assert back.topic == request.topic
        assert back.routing_policy.priority == 50
        assert row.subscribers == ["bi.assistant"]
        assert row.status == "queued"


def test_dedup_match_lookup(tmp_path: Path) -> None:
    session_factory = _session_factory(tmp_path)
    request = ContextRequest(
        kind="glossary.definition",
        topic="revenue metrics",
        question="What does active customer mean?",
        consumer="bi.assistant",
        dedup_key="glossary:dim_customers.active_flag",
    )

    with session_factory() as session:
        session.add(request_to_row(request))
        session.commit()

    since = utcnow() - timedelta(hours=1)
    with session_factory() as session:
        match = repo.find_open_dedup_match(
            session, org_id="default", dedup_key=request.dedup_key or "", since=since
        )
        assert match is not None
        assert match.id == request.id

        no_match = repo.find_resolved_dedup_match(
            session, org_id="default", dedup_key=request.dedup_key or "", since=since
        )
        assert no_match is None


def test_set_asked_and_lookup_by_thread_ref(tmp_path: Path) -> None:
    session_factory = _session_factory(tmp_path)
    request = ContextRequest(
        kind="glossary.definition",
        topic="revenue metrics",
        question="What does active customer mean?",
        consumer="bi.assistant",
    )

    with session_factory() as session:
        session.add(request_to_row(request))
        session.commit()

    with session_factory() as session:
        repo.set_asked(
            session,
            request.id,
            expert_id="U1",
            asked_at=utcnow(),
            thread_ref="thread-abc",
        )
        session.commit()

    with session_factory() as session:
        row = repo.get_request_by_thread_ref(session, "thread-abc")
        assert row is not None
        assert row.id == request.id
        assert row.status == "asked"
        assert row.asked_expert_id == "U1"
