from datetime import timedelta
from pathlib import Path

import pytest

from shouldertap.engine.clock import ManualClock
from shouldertap.engine.contracts import (
    ConsumerRegistration,
    ContextProposal,
    ContextRequest,
    InProcessDelivery,
)
from shouldertap.engine.delivery import ConsumerDeliverer
from shouldertap.engine.facade import Facade
from shouldertap.engine.registry import RegistryConfig
from shouldertap.engine.scheduler.core import make_scheduler
from shouldertap.engine.store.engine import make_engine, make_session_factory
from shouldertap.engine.store.migrate import run_migrations
from shouldertap.engine.store.repository import (
    get_request,
    list_audit_events,
    list_pending_proposals,
    upsert_consumer,
    upsert_expert,
)
from shouldertap.engine.transports.console import ConsoleTransport


class RecordingCallbacks:
    def __init__(self) -> None:
        self.accepted: list[ContextProposal] = []
        self.failed: list[tuple[str, object]] = []

    def on_proposal(self, proposal: ContextProposal) -> None:
        pass

    def on_proposal_accepted(self, proposal: ContextProposal) -> None:
        self.accepted.append(proposal)

    def on_proposal_rejected(self, proposal: ContextProposal, reason: str) -> None:
        pass

    def on_request_failed(self, request_id: str, reason: object) -> None:
        self.failed.append((request_id, reason))


class FakeLLM:
    def draft_question(self, prompt: str) -> str | None:
        return "Quick one: what counts as an active customer?"

    def structure_answer(self, prompt: str):
        return {"definition": "paying accounts active in 90 days", "caveats": []}, 0.9


def _config(**overrides) -> RegistryConfig:
    payload = {"org": {"name": "Test", "timezone": "UTC"}}
    payload.update(overrides)
    return RegistryConfig.model_validate(payload)


@pytest.fixture
def wiring(tmp_path: Path):
    db_path = tmp_path / "shouldertap.db"
    run_migrations(db_path)
    session_factory = make_session_factory(make_engine(db_path))
    scheduler = make_scheduler(db_path)
    return session_factory, scheduler


def _register_expert_and_consumer(session_factory, deliverer: ConsumerDeliverer, callbacks) -> None:
    with session_factory() as session:
        upsert_expert(
            session, expert_id="U1", name="Dana", topics=["revenue metrics"], escalation_to=None
        )
        upsert_consumer(
            session,
            ConsumerRegistration(
                id="bi.assistant",
                handles_kinds=["glossary.definition"],
                delivery=InProcessDelivery(),
            ),
        )
        session.commit()
    deliverer.register_in_process("bi.assistant", callbacks)


def test_golden_path_end_to_end(wiring) -> None:
    session_factory, scheduler = wiring
    clock = ManualClock()
    transport = ConsoleTransport(interactive=False)
    deliverer = ConsumerDeliverer()
    callbacks = RecordingCallbacks()
    _register_expert_and_consumer(session_factory, deliverer, callbacks)

    facade = Facade(
        session_factory=session_factory,
        scheduler=scheduler,
        config=_config(),
        transport=transport,
        llm_provider=FakeLLM(),
        deliverer=deliverer,
        clock=clock,
    )

    request = ContextRequest(
        kind="glossary.definition",
        topic="revenue metrics",
        question="What does active customer mean?",
        consumer="bi.assistant",
        context={"asked_because": "the BI agent hit a low-confidence answer"},
    )
    submit_outcome = facade.submit_request(request)

    assert submit_outcome.status == "queued"
    assert len(transport.sent_asks) == 1

    transport.push_reply("paying accounts active in 90 days")

    with session_factory() as session:
        pending = list_pending_proposals(session)
        assert len(pending) == 1
        proposal_id = pending[0].id
        assert pending[0].structured == {
            "definition": "paying accounts active in 90 days",
            "caveats": [],
        }

    accept_result = facade.accept_proposal(proposal_id=proposal_id, decided_by="alice")

    assert accept_result is not None
    assert len(callbacks.accepted) == 1
    assert callbacks.accepted[0].id == proposal_id
    # one "got it, sending for review" ack at capture time (§8.1), one attribution
    # notification at accept time (§9).
    assert len(transport.sent_notifications) == 2
    assert "accepted" in transport.sent_notifications[1][1].lower()

    with session_factory() as session:
        req = get_request(session, request.id)
        assert req.status == "accepted"

        events = [e.event for e in list_audit_events(session, request.id)]
        for expected in [
            "request.received",
            "routing.resolved",
            "ask.sent",
            "reply.received",
            "proposal.created",
            "proposal.accepted",
        ]:
            assert expected in events, f"missing audit event {expected}"


def test_zero_llm_degrades_to_verbatim_question_and_null_structured(wiring) -> None:
    session_factory, scheduler = wiring
    clock = ManualClock()
    transport = ConsoleTransport(interactive=False)
    deliverer = ConsumerDeliverer()
    callbacks = RecordingCallbacks()
    _register_expert_and_consumer(session_factory, deliverer, callbacks)

    facade = Facade(
        session_factory=session_factory,
        scheduler=scheduler,
        config=_config(),
        transport=transport,
        llm_provider=None,  # no LLM configured
        deliverer=deliverer,
        clock=clock,
    )

    request = ContextRequest(
        kind="glossary.definition",
        topic="revenue metrics",
        question="What does active customer mean?",
        consumer="bi.assistant",
    )
    facade.submit_request(request)
    transport.push_reply("paying accounts")

    with session_factory() as session:
        pending = list_pending_proposals(session)
        assert len(pending) == 1
        assert pending[0].structured is None
        assert pending[0].answer == "paying accounts"


def test_dedup_resolved_delivers_immediately_to_new_consumer(wiring) -> None:
    session_factory, scheduler = wiring
    clock = ManualClock()
    transport = ConsoleTransport(interactive=False)
    deliverer = ConsumerDeliverer()
    callbacks = RecordingCallbacks()
    _register_expert_and_consumer(session_factory, deliverer, callbacks)
    second_callbacks = RecordingCallbacks()
    with session_factory() as session:
        upsert_consumer(
            session,
            ConsumerRegistration(
                id="second.consumer",
                handles_kinds=["glossary.definition"],
                delivery=InProcessDelivery(),
            ),
        )
        session.commit()
    deliverer.register_in_process("second.consumer", second_callbacks)

    facade = Facade(
        session_factory=session_factory,
        scheduler=scheduler,
        config=_config(),
        transport=transport,
        llm_provider=FakeLLM(),
        deliverer=deliverer,
        clock=clock,
    )

    request = ContextRequest(
        kind="glossary.definition",
        topic="revenue metrics",
        question="What does active customer mean?",
        consumer="bi.assistant",
        dedup_key="glossary:dim_customers.active_flag",
    )
    facade.submit_request(request)
    transport.push_reply("paying accounts active in 90 days")

    with session_factory() as session:
        pending = list_pending_proposals(session)
        proposal_id = pending[0].id
    facade.accept_proposal(proposal_id=proposal_id, decided_by="alice")

    second_request = ContextRequest(
        kind="glossary.definition",
        topic="revenue metrics",
        question="What does active customer mean?",
        consumer="second.consumer",
        dedup_key="glossary:dim_customers.active_flag",
    )
    outcome = facade.submit_request(second_request)

    assert outcome.status == "deduped_resolved"
    assert outcome.proposal is not None
    assert len(second_callbacks.accepted) == 1
    assert len(transport.sent_asks) == 1  # the expert was asked exactly once


def test_dedup_open_attaches_subscriber_without_a_second_ask(wiring) -> None:
    session_factory, scheduler = wiring
    clock = ManualClock()
    transport = ConsoleTransport(interactive=False)
    deliverer = ConsumerDeliverer()
    callbacks = RecordingCallbacks()
    _register_expert_and_consumer(session_factory, deliverer, callbacks)

    facade = Facade(
        session_factory=session_factory,
        scheduler=scheduler,
        config=_config(),
        transport=transport,
        llm_provider=FakeLLM(),
        deliverer=deliverer,
        clock=clock,
    )

    request = ContextRequest(
        kind="glossary.definition",
        topic="revenue metrics",
        question="What does active customer mean?",
        consumer="bi.assistant",
        dedup_key="glossary:dim_customers.active_flag",
    )
    facade.submit_request(request)

    second_request = ContextRequest(
        kind="glossary.definition",
        topic="revenue metrics",
        question="What does active customer mean?",
        consumer="bi.assistant",
        dedup_key="glossary:dim_customers.active_flag",
    )
    outcome = facade.submit_request(second_request)

    assert outcome.status == "deduped_open"
    assert len(transport.sent_asks) == 1  # only asked once


def test_give_up_timer_fails_request_and_notifies_consumer(wiring) -> None:
    session_factory, scheduler = wiring
    clock = ManualClock()
    transport = ConsoleTransport(interactive=False)
    deliverer = ConsumerDeliverer()
    callbacks = RecordingCallbacks()
    _register_expert_and_consumer(session_factory, deliverer, callbacks)

    facade = Facade(
        session_factory=session_factory,
        scheduler=scheduler,
        config=_config(),
        transport=transport,
        llm_provider=FakeLLM(),
        deliverer=deliverer,
        clock=clock,
    )

    request = ContextRequest(
        kind="glossary.definition",
        topic="revenue metrics",
        question="What does active customer mean?",
        consumer="bi.assistant",
    )
    facade.submit_request(request)

    clock.advance(timedelta(hours=25))  # past the default 24h give_up_after
    facade.handle_give_up_timer(request.id)

    assert len(callbacks.failed) == 1
    assert callbacks.failed[0][0] == request.id
    assert callbacks.failed[0][1].code == "timeout"

    with session_factory() as session:
        req = get_request(session, request.id)
        assert req.status == "failed"


def test_escalation_timer_reasks_configured_escalation_target(wiring) -> None:
    session_factory, scheduler = wiring
    clock = ManualClock()
    transport = ConsoleTransport(interactive=False)
    deliverer = ConsumerDeliverer()
    callbacks = RecordingCallbacks()

    with session_factory() as session:
        upsert_expert(
            session, expert_id="U1", name="Dana", topics=["revenue metrics"], escalation_to="U2"
        )
        upsert_expert(session, expert_id="U2", name="Marco", topics=[], escalation_to=None)
        upsert_consumer(
            session,
            ConsumerRegistration(
                id="bi.assistant",
                handles_kinds=["glossary.definition"],
                delivery=InProcessDelivery(),
            ),
        )
        session.commit()
    deliverer.register_in_process("bi.assistant", callbacks)

    config = _config(
        experts=[
            {"id": "U1", "name": "Dana", "topics": ["revenue metrics"], "escalation_to": "U2"},
            {"id": "U2", "name": "Marco", "topics": []},
        ]
    )
    facade = Facade(
        session_factory=session_factory,
        scheduler=scheduler,
        config=config,
        transport=transport,
        llm_provider=FakeLLM(),
        deliverer=deliverer,
        clock=clock,
    )

    request = ContextRequest(
        kind="glossary.definition",
        topic="revenue metrics",
        question="What does active customer mean?",
        consumer="bi.assistant",
    )
    facade.submit_request(request)
    assert len(transport.sent_asks) == 1

    clock.advance(timedelta(hours=3))  # past the default 2h escalation_after
    facade.handle_escalation_timer(request.id)

    assert len(transport.sent_asks) == 2  # escalated to U2

    with session_factory() as session:
        req = get_request(session, request.id)
        assert req.escalated is True
        assert req.asked_expert_id == "U2"
        events = [e.event for e in list_audit_events(session, request.id)]
        assert "escalation.fired" in events
