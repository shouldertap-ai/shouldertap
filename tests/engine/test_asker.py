from datetime import datetime, time
from pathlib import Path

import pytest

from shouldertap.engine import asker
from shouldertap.engine.clock import ManualClock
from shouldertap.engine.contracts import ContextRequest
from shouldertap.engine.registry import RegistryConfig
from shouldertap.engine.scheduler.core import make_scheduler
from shouldertap.engine.store.engine import make_engine, make_session_factory
from shouldertap.engine.store.migrate import run_migrations
from shouldertap.engine.store.repository import (
    adjust_open_asks,
    get_expert,
    list_audit_events,
    record_ask_today,
    upsert_expert,
)
from shouldertap.engine.transports.console import ConsoleTransport


@pytest.fixture
def session_factory(tmp_path: Path):
    db_path = tmp_path / "shouldertap.db"
    run_migrations(db_path)
    return make_session_factory(make_engine(db_path))


@pytest.fixture
def scheduler(tmp_path: Path):
    db_path = tmp_path / "shouldertap.db"
    sched = make_scheduler(db_path)
    yield sched


def _config(**overrides) -> RegistryConfig:
    payload = {"org": {"name": "Test", "timezone": "UTC"}}
    payload.update(overrides)
    return RegistryConfig.model_validate(payload)


def _request(**kwargs) -> ContextRequest:
    defaults = dict(
        kind="glossary.definition",
        topic="revenue metrics",
        question="What does active customer mean?",
        consumer="bi.assistant",
    )
    defaults.update(kwargs)
    return ContextRequest(**defaults)


class FakeLLM:
    def __init__(self, drafted: str | None) -> None:
        self.drafted = drafted
        self.prompts: list[str] = []

    def draft_question(self, prompt: str) -> str | None:
        self.prompts.append(prompt)
        return self.drafted

    def structure_answer(self, prompt: str):
        raise NotImplementedError


def test_compose_message_contains_six_elements_in_order() -> None:
    request = _request(context={"asked_because": "the agent hit a low-confidence answer"})
    message = asker.compose_message(request=request, expert_name="Dana", question_text="What is X?")

    self_id = message.index("I'm ShoulderTap, an automated assistant")
    why = message.index("the agent hit a low-confidence answer")
    question = message.index("What is X?")
    effort = message.index("A one-or-two sentence reply here is all that's needed.")
    attribution = message.index("Your answer will be recorded with your name as the source")
    opt_out = message.index("Reply `mute` to stop receiving asks")

    assert self_id < why < question < effort < attribution < opt_out


def test_draft_question_text_falls_back_to_verbatim_when_no_llm() -> None:
    request = _request(question="What does active customer mean?")
    assert asker.draft_question_text(None, request=request, expert_name="Dana") == request.question


def test_draft_question_text_falls_back_to_verbatim_when_llm_returns_none() -> None:
    request = _request()
    llm = FakeLLM(drafted=None)
    assert asker.draft_question_text(llm, request=request, expert_name="Dana") == request.question


def test_draft_question_text_uses_llm_output_when_available() -> None:
    request = _request()
    llm = FakeLLM(drafted="Quick one: what counts as an active customer?")
    result = asker.draft_question_text(llm, request=request, expert_name="Dana")
    assert result == "Quick one: what counts as an active customer?"
    assert "Dana" in llm.prompts[0]


def test_is_within_quiet_hours_non_crossing_window() -> None:
    quiet_hours = (time(1, 0), time(5, 0))
    assert asker.is_within_quiet_hours(datetime(2026, 1, 1, 2, 0), quiet_hours, "UTC") is True
    assert asker.is_within_quiet_hours(datetime(2026, 1, 1, 6, 0), quiet_hours, "UTC") is False


def test_is_within_quiet_hours_crossing_midnight() -> None:
    quiet_hours = (time(18, 0), time(9, 0))
    assert asker.is_within_quiet_hours(datetime(2026, 1, 1, 20, 0), quiet_hours, "UTC") is True
    assert asker.is_within_quiet_hours(datetime(2026, 1, 2, 3, 0), quiet_hours, "UTC") is True
    assert asker.is_within_quiet_hours(datetime(2026, 1, 1, 12, 0), quiet_hours, "UTC") is False


def test_is_within_quiet_hours_none_means_never_queued() -> None:
    assert asker.is_within_quiet_hours(datetime(2026, 1, 1, 20, 0), None, "UTC") is False


def test_is_capped_by_open_asks(session_factory) -> None:
    with session_factory() as session:
        upsert_expert(session, expert_id="U1", name="Dana", topics=[], escalation_to=None)
        adjust_open_asks(session, "U1", 3)
        session.commit()
        expert = get_expert(session, "U1")
        config = _config(defaults={"max_open_asks_per_expert": 3, "max_asks_per_expert_per_day": 5})
        assert asker.is_capped(expert, config.defaults, "2026-01-01") is True


def test_is_capped_daily_cap_ignores_stale_date(session_factory) -> None:
    with session_factory() as session:
        upsert_expert(session, expert_id="U1", name="Dana", topics=[], escalation_to=None)
        record_ask_today(session, "U1", "2025-12-31")
        record_ask_today(session, "U1", "2025-12-31")
        session.commit()
        expert = get_expert(session, "U1")
        config = _config(defaults={"max_open_asks_per_expert": 3, "max_asks_per_expert_per_day": 1})
        # asks_today_date is yesterday, so today's effective count should be 0, not capped.
        assert asker.is_capped(expert, config.defaults, "2026-01-01") is False
        assert asker.is_capped(expert, config.defaults, "2025-12-31") is True


def test_route_and_ask_delivers_and_schedules_escalation(session_factory, scheduler) -> None:
    with session_factory() as session:
        upsert_expert(
            session, expert_id="U1", name="Dana", topics=["revenue metrics"], escalation_to=None
        )
        session.commit()

    request = _request()
    transport = ConsoleTransport(interactive=False)
    clock = ManualClock()
    config = _config()

    with session_factory() as session:
        from shouldertap.engine.store.mappers import request_to_row

        session.add(request_to_row(request))
        session.commit()

        outcome = asker.route_and_ask(session, scheduler, config, transport, None, clock, request)
        session.commit()

    assert outcome.status == "asked"
    assert outcome.expert_id == "U1"
    assert len(transport.sent_asks) == 1

    with session_factory() as session:
        events = [e.event for e in list_audit_events(session, request.id)]
        assert "ask.sent" in events
        expert = get_expert(session, "U1")
        assert expert.open_asks == 1


def test_route_and_ask_no_expert_found(session_factory, scheduler) -> None:
    request = _request(topic="totally unmatched")
    transport = ConsoleTransport(interactive=False)
    clock = ManualClock()
    config = _config()

    with session_factory() as session:
        from shouldertap.engine.store.mappers import request_to_row

        session.add(request_to_row(request))
        session.commit()
        outcome = asker.route_and_ask(session, scheduler, config, transport, None, clock, request)

    assert outcome.status == "no_expert_found"
    assert transport.sent_asks == []


def test_route_and_ask_routes_around_capped_expert(session_factory, scheduler) -> None:
    """U1 has fewer open_asks than U2 (0 vs 1), so the router's load-balancing step would
    normally pick U1 first -- but U1 has already exhausted today's per-expert cap, a limit the
    router itself doesn't know about. The retry-past-capped-candidates logic must skip U1 and
    fall through to U2, which the router alone would not have chosen.
    """
    clock = ManualClock()
    today = asker.today_str(clock)

    with session_factory() as session:
        upsert_expert(
            session, expert_id="U1", name="Dana", topics=["revenue metrics"], escalation_to=None
        )
        upsert_expert(
            session, expert_id="U2", name="Marco", topics=["revenue metrics"], escalation_to=None
        )
        record_ask_today(session, "U1", today)
        adjust_open_asks(session, "U2", 1)
        session.commit()

    request = _request()
    transport = ConsoleTransport(interactive=False)
    config = _config(defaults={"max_open_asks_per_expert": 3, "max_asks_per_expert_per_day": 1})

    with session_factory() as session:
        from shouldertap.engine.store.mappers import request_to_row

        session.add(request_to_row(request))
        session.commit()
        outcome = asker.route_and_ask(session, scheduler, config, transport, None, clock, request)
        session.commit()

    assert outcome.status == "asked"
    assert outcome.expert_id == "U2"

    with session_factory() as session:
        events = [e.event for e in list_audit_events(session, request.id)]
        assert "ask.rate_limited" in events
        assert "ask.sent" in events


def test_route_and_ask_holds_when_all_candidates_capped(session_factory, scheduler) -> None:
    with session_factory() as session:
        upsert_expert(
            session, expert_id="U1", name="Dana", topics=["revenue metrics"], escalation_to=None
        )
        adjust_open_asks(session, "U1", 3)
        session.commit()

    request = _request()
    transport = ConsoleTransport(interactive=False)
    clock = ManualClock()
    config = _config(defaults={"max_open_asks_per_expert": 3, "max_asks_per_expert_per_day": 5})

    with session_factory() as session:
        from shouldertap.engine.store.mappers import request_to_row

        session.add(request_to_row(request))
        session.commit()
        outcome = asker.route_and_ask(session, scheduler, config, transport, None, clock, request)

    assert outcome.status == "rate_limited_hold"
    assert transport.sent_asks == []


def test_route_and_ask_queues_during_quiet_hours(session_factory, scheduler) -> None:
    with session_factory() as session:
        upsert_expert(
            session, expert_id="U1", name="Dana", topics=["revenue metrics"], escalation_to=None
        )
        session.commit()

    request = _request()
    transport = ConsoleTransport(interactive=False)
    clock = ManualClock(datetime(2026, 1, 1, 20, 0))  # inside the 18:00-09:00 window
    config = _config(defaults={"quiet_hours": ["18:00", "09:00"]})

    with session_factory() as session:
        from shouldertap.engine.store.mappers import request_to_row

        session.add(request_to_row(request))
        session.commit()
        outcome = asker.route_and_ask(session, scheduler, config, transport, None, clock, request)
        session.commit()

    assert outcome.status == "queued_quiet_hours"
    assert transport.sent_asks == []

    with session_factory() as session:
        events = [e.event for e in list_audit_events(session, request.id)]
        assert "ask.queued_quiet_hours" in events
