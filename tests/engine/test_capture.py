from datetime import datetime
from pathlib import Path

import pytest

from shouldertap.engine import capture
from shouldertap.engine.clock import ManualClock
from shouldertap.engine.contracts import ContextRequest
from shouldertap.engine.delivery import ConsumerDeliverer
from shouldertap.engine.registry import RegistryConfig
from shouldertap.engine.scheduler.core import make_scheduler
from shouldertap.engine.store.engine import make_engine, make_session_factory
from shouldertap.engine.store.mappers import request_to_row
from shouldertap.engine.store.migrate import run_migrations
from shouldertap.engine.store.repository import (
    decide_proposal,
    get_expert,
    get_proposal,
    get_request,
    list_audit_events,
    set_asked,
    upsert_expert,
)
from shouldertap.engine.transports.console import ConsoleTransport
from shouldertap.engine.transports.types import IncomingReply


@pytest.fixture
def session_factory(tmp_path: Path):
    db_path = tmp_path / "shouldertap.db"
    run_migrations(db_path)
    return make_session_factory(make_engine(db_path))


@pytest.fixture
def scheduler(tmp_path: Path):
    db_path = tmp_path / "shouldertap.db"
    return make_scheduler(db_path)


@pytest.fixture
def deliverer() -> ConsumerDeliverer:
    return ConsumerDeliverer()


def _config(**overrides) -> RegistryConfig:
    payload = {"org": {"name": "Test", "timezone": "UTC"}}
    payload.update(overrides)
    return RegistryConfig.model_validate(payload)


def _seed_asked_request(
    session_factory, *, expert_id: str = "U1", kind: str = "glossary.definition"
):
    request = ContextRequest(
        kind=kind,
        topic="revenue metrics",
        question="What does active customer mean?",
        consumer="bi.assistant",
    )
    with session_factory() as session:
        upsert_expert(
            session,
            expert_id=expert_id,
            name="Dana",
            topics=["revenue metrics"],
            escalation_to=None,
        )
        session.add(request_to_row(request))
        session.commit()
        set_asked(
            session,
            request.id,
            expert_id=expert_id,
            asked_at=datetime(2026, 1, 1),
            thread_ref="thread-1",
        )
        session.commit()
    return request


class FakeLLM:
    def __init__(self, structured, confidence) -> None:
        self._structured = structured
        self._confidence = confidence

    def draft_question(self, prompt: str):
        raise NotImplementedError

    def structure_answer(self, prompt: str):
        return self._structured, self._confidence


def test_mute_sets_flag_and_audits(session_factory, scheduler, deliverer) -> None:
    request = _seed_asked_request(session_factory)
    config = _config()
    clock = ManualClock()
    transport = ConsoleTransport(interactive=False)

    with session_factory() as session:
        outcome = capture.handle_reply(
            session,
            scheduler,
            config,
            transport,
            None,
            deliverer,
            clock,
            IncomingReply(
                thread_ref="thread-1", expert_id="U1", text="mute", received_at=clock.now()
            ),
        )
        session.commit()

    assert outcome.kind == "mute"
    with session_factory() as session:
        expert = get_expert(session, "U1")
        assert expert.muted is True
        events = [e.event for e in list_audit_events(session, request.id)]
        assert "expert.muted" in events


def test_skip_reroutes_to_escalation_target(session_factory, scheduler, deliverer) -> None:
    request = _seed_asked_request(session_factory, expert_id="U1")
    with session_factory() as session:
        upsert_expert(session, expert_id="U2", name="Marco", topics=[], escalation_to=None)
        # wire U1's escalation_to via config, not the DB row -- see resolve_escalation_target
        session.commit()

    config = _config(
        experts=[{"id": "U1", "name": "Dana", "topics": ["revenue metrics"], "escalation_to": "U2"}]
    )
    clock = ManualClock()
    transport = ConsoleTransport(interactive=False)

    with session_factory() as session:
        outcome = capture.handle_reply(
            session,
            scheduler,
            config,
            transport,
            None,
            deliverer,
            clock,
            IncomingReply(
                thread_ref="thread-1", expert_id="U1", text="skip", received_at=clock.now()
            ),
        )
        session.commit()

    assert outcome.kind == "skip_rerouted"
    assert outcome.ask_outcome is not None
    assert outcome.ask_outcome.status == "asked"
    assert outcome.ask_outcome.expert_id == "U2"

    with session_factory() as session:
        events = [e.event for e in list_audit_events(session, request.id)]
        assert "expert.skipped" in events
        expert_u1 = get_expert(session, "U1")
        assert expert_u1.open_asks == 0


def test_skip_with_no_escalation_target(session_factory, scheduler, deliverer) -> None:
    request = _seed_asked_request(session_factory, expert_id="U1")
    config = _config()  # no escalation_to configured anywhere
    clock = ManualClock()
    transport = ConsoleTransport(interactive=False)

    with session_factory() as session:
        outcome = capture.handle_reply(
            session,
            scheduler,
            config,
            transport,
            None,
            deliverer,
            clock,
            IncomingReply(
                thread_ref="thread-1", expert_id="U1", text="skip", received_at=clock.now()
            ),
        )

    assert outcome.kind == "skip_no_target"
    assert outcome.request_id == request.id


def test_unknown_thread_ref_is_ignored(session_factory, scheduler, deliverer) -> None:
    config = _config()
    clock = ManualClock()
    transport = ConsoleTransport(interactive=False)

    with session_factory() as session:
        outcome = capture.handle_reply(
            session,
            scheduler,
            config,
            transport,
            None,
            deliverer,
            clock,
            IncomingReply(
                thread_ref="nope", expert_id="U1", text="an answer", received_at=clock.now()
            ),
        )

    assert outcome.kind == "ignored"


def test_first_substantive_reply_creates_pending_proposal_with_structuring(
    session_factory, scheduler, deliverer
) -> None:
    request = _seed_asked_request(session_factory)
    config = _config()
    clock = ManualClock()
    transport = ConsoleTransport(interactive=False)
    llm = FakeLLM(
        structured={"definition": "paying accounts active in 90d", "caveats": []}, confidence=0.85
    )

    with session_factory() as session:
        outcome = capture.handle_reply(
            session,
            scheduler,
            config,
            transport,
            llm,
            deliverer,
            clock,
            IncomingReply(
                thread_ref="thread-1",
                expert_id="U1",
                text="paying accounts active in 90d",
                received_at=clock.now(),
            ),
        )
        session.commit()

    assert outcome.kind == "answer"
    assert outcome.proposal_id is not None

    with session_factory() as session:
        proposal = get_proposal(session, outcome.proposal_id)
        assert proposal.status == "pending"
        assert proposal.structured == {"definition": "paying accounts active in 90d", "caveats": []}
        assert proposal.confidence == 0.85

        req = get_request(session, request.id)
        assert req.status == "proposed"

        expert = get_expert(session, "U1")
        assert expert.open_asks == 0

        events = [e.event for e in list_audit_events(session, request.id)]
        assert "reply.received" in events
        assert "proposal.created" in events


def test_first_substantive_reply_without_llm_leaves_structured_null(
    session_factory, scheduler, deliverer
) -> None:
    _seed_asked_request(session_factory)
    config = _config()
    clock = ManualClock()
    transport = ConsoleTransport(interactive=False)

    with session_factory() as session:
        outcome = capture.handle_reply(
            session,
            scheduler,
            config,
            transport,
            None,
            deliverer,
            clock,
            IncomingReply(
                thread_ref="thread-1",
                expert_id="U1",
                text="paying accounts",
                received_at=clock.now(),
            ),
        )
        session.commit()

    with session_factory() as session:
        proposal = get_proposal(session, outcome.proposal_id)
        assert proposal.structured is None
        assert proposal.answer == "paying accounts"


def test_low_confidence_structuring_still_creates_proposal_with_null_structured(
    session_factory, scheduler, deliverer
) -> None:
    _seed_asked_request(session_factory)
    config = _config()
    clock = ManualClock()
    transport = ConsoleTransport(interactive=False)
    llm = FakeLLM(structured={"definition": "x", "caveats": []}, confidence=0.1)

    with session_factory() as session:
        outcome = capture.handle_reply(
            session,
            scheduler,
            config,
            transport,
            llm,
            deliverer,
            clock,
            IncomingReply(
                thread_ref="thread-1", expert_id="U1", text="some answer", received_at=clock.now()
            ),
        )
        session.commit()

    with session_factory() as session:
        proposal = get_proposal(session, outcome.proposal_id)
        assert proposal.structured is None
        assert proposal.confidence == 0.1


def test_amendment_appends_to_pending_proposal(session_factory, scheduler, deliverer) -> None:
    _seed_asked_request(session_factory)
    config = _config()
    clock = ManualClock()
    transport = ConsoleTransport(interactive=False)

    with session_factory() as session:
        first = capture.handle_reply(
            session,
            scheduler,
            config,
            transport,
            None,
            deliverer,
            clock,
            IncomingReply(
                thread_ref="thread-1", expert_id="U1", text="first answer", received_at=clock.now()
            ),
        )
        session.commit()

    with session_factory() as session:
        second = capture.handle_reply(
            session,
            scheduler,
            config,
            transport,
            None,
            deliverer,
            clock,
            IncomingReply(
                thread_ref="thread-1",
                expert_id="U1",
                text="oh also a caveat",
                received_at=clock.now(),
            ),
        )
        session.commit()

    assert second.kind == "amendment"
    assert second.proposal_id == first.proposal_id
    with session_factory() as session:
        proposal = get_proposal(session, first.proposal_id)
        assert "first answer" in proposal.answer
        assert "oh also a caveat" in proposal.answer


def test_reply_after_decision_is_ignored_but_logged(session_factory, scheduler, deliverer) -> None:
    request = _seed_asked_request(session_factory)
    config = _config()
    clock = ManualClock()
    transport = ConsoleTransport(interactive=False)

    with session_factory() as session:
        first = capture.handle_reply(
            session,
            scheduler,
            config,
            transport,
            None,
            deliverer,
            clock,
            IncomingReply(
                thread_ref="thread-1", expert_id="U1", text="first answer", received_at=clock.now()
            ),
        )
        session.commit()

    with session_factory() as session:
        decide_proposal(
            session,
            first.proposal_id,
            status="accepted",
            decided_by="alice",
            decided_at=clock.now(),
        )
        session.commit()

    with session_factory() as session:
        outcome = capture.handle_reply(
            session,
            scheduler,
            config,
            transport,
            None,
            deliverer,
            clock,
            IncomingReply(
                thread_ref="thread-1",
                expert_id="U1",
                text="one more thing",
                received_at=clock.now(),
            ),
        )
        session.commit()

    assert outcome.kind == "ignored"
    with session_factory() as session:
        proposal = get_proposal(session, first.proposal_id)
        assert "one more thing" not in proposal.answer  # never appended
        events = list_audit_events(session, request.id)
        ignored_events = [e for e in events if e.detail and e.detail.get("ignored")]
        assert len(ignored_events) == 1
