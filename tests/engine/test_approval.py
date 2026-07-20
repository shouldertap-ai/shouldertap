from pathlib import Path

import pytest

from shouldertap.engine import approval
from shouldertap.engine.clock import ManualClock
from shouldertap.engine.contracts import (
    ConsumerRegistration,
    ContextProposal,
    ContextRequest,
    InProcessDelivery,
    Provenance,
)
from shouldertap.engine.delivery import ConsumerDeliverer
from shouldertap.engine.store.engine import make_engine, make_session_factory
from shouldertap.engine.store.mappers import proposal_to_row, request_to_row
from shouldertap.engine.store.migrate import run_migrations
from shouldertap.engine.store.repository import (
    add_subscriber,
    get_proposal,
    get_request,
    upsert_consumer,
)
from shouldertap.engine.transports.console import ConsoleTransport


@pytest.fixture
def session_factory(tmp_path: Path):
    db_path = tmp_path / "shouldertap.db"
    run_migrations(db_path)
    return make_session_factory(make_engine(db_path))


class RecordingCallbacks:
    def __init__(self) -> None:
        self.accepted: list[ContextProposal] = []
        self.rejected: list[tuple[ContextProposal, str]] = []
        self.proposals: list[ContextProposal] = []

    def on_proposal(self, proposal: ContextProposal) -> None:
        self.proposals.append(proposal)

    def on_proposal_accepted(self, proposal: ContextProposal) -> None:
        self.accepted.append(proposal)

    def on_proposal_rejected(self, proposal: ContextProposal, reason: str) -> None:
        self.rejected.append((proposal, reason))

    def on_request_failed(self, request_id: str, reason) -> None:
        raise NotImplementedError


def _seed_pending_proposal(session_factory, *, consumers: list[str] = ("bi.assistant",)):
    request = ContextRequest(
        kind="glossary.definition",
        topic="revenue metrics",
        question="What does active customer mean?",
        consumer=consumers[0],
    )
    proposal = ContextProposal(
        request_id=request.id,
        kind=request.kind,
        answer="paying accounts active in 90d",
        provenance=Provenance(
            expert_id="U1",
            expert_name="Dana",
            answered_via="console",
            answered_at=request.created_at,
        ),
        consumer=consumers[0],
    )
    with session_factory() as session:
        session.add(request_to_row(request))
        session.add(proposal_to_row(proposal, status="pending"))
        for consumer_id in consumers:
            upsert_consumer(
                session,
                ConsumerRegistration(
                    id=consumer_id,
                    handles_kinds=["glossary.definition"],
                    delivery=InProcessDelivery(),
                ),
            )
        session.commit()
        for extra_consumer in consumers[1:]:
            add_subscriber(session, request.id, extra_consumer)
        session.commit()
    return request, proposal


def test_accept_proposal_marks_accepted_and_resolves_request(session_factory) -> None:
    request, proposal = _seed_pending_proposal(session_factory)
    deliverer = ConsumerDeliverer()
    transport = ConsoleTransport(interactive=False)
    clock = ManualClock()

    with session_factory() as session:
        result = approval.accept_proposal(
            session, deliverer, transport, clock, proposal_id=proposal.id, decided_by="alice"
        )
        session.commit()

    assert result is not None
    with session_factory() as session:
        row = get_proposal(session, proposal.id)
        assert row.status == "accepted"
        assert row.decided_by == "alice"

        req = get_request(session, request.id)
        assert req.status == "accepted"
        assert req.resolved_proposal_id == proposal.id


def test_accept_proposal_notifies_expert_with_attribution(session_factory) -> None:
    request, proposal = _seed_pending_proposal(session_factory)
    deliverer = ConsumerDeliverer()
    transport = ConsoleTransport(interactive=False)
    clock = ManualClock()

    with session_factory() as session:
        approval.accept_proposal(
            session, deliverer, transport, clock, proposal_id=proposal.id, decided_by="alice"
        )
        session.commit()

    assert len(transport.sent_notifications) == 1
    expert_id, message = transport.sent_notifications[0]
    assert expert_id == "U1"
    assert "Dana" in message
    assert "accepted" in message.lower()


def test_accept_proposal_fans_out_to_all_subscribers(session_factory) -> None:
    request, proposal = _seed_pending_proposal(
        session_factory, consumers=["bi.assistant", "other.consumer"]
    )
    deliverer = ConsumerDeliverer()
    bi_callbacks = RecordingCallbacks()
    other_callbacks = RecordingCallbacks()
    deliverer.register_in_process("bi.assistant", bi_callbacks)
    deliverer.register_in_process("other.consumer", other_callbacks)
    transport = ConsoleTransport(interactive=False)
    clock = ManualClock()

    with session_factory() as session:
        approval.accept_proposal(
            session, deliverer, transport, clock, proposal_id=proposal.id, decided_by="alice"
        )
        session.commit()

    assert len(bi_callbacks.accepted) == 1
    assert len(other_callbacks.accepted) == 1
    assert bi_callbacks.accepted[0].id == proposal.id


def test_reject_proposal_marks_rejected_and_notifies_subscribers(session_factory) -> None:
    request, proposal = _seed_pending_proposal(session_factory)
    deliverer = ConsumerDeliverer()
    callbacks = RecordingCallbacks()
    deliverer.register_in_process("bi.assistant", callbacks)
    clock = ManualClock()

    with session_factory() as session:
        result = approval.reject_proposal(
            session,
            deliverer,
            clock,
            proposal_id=proposal.id,
            decided_by="alice",
            reason="not accurate",
        )
        session.commit()

    assert result is not None
    assert len(callbacks.rejected) == 1
    assert callbacks.rejected[0][1] == "not accurate"

    with session_factory() as session:
        row = get_proposal(session, proposal.id)
        assert row.status == "rejected"
        req = get_request(session, request.id)
        assert req.status == "rejected"


def test_accepting_an_already_decided_proposal_is_a_no_op(session_factory) -> None:
    request, proposal = _seed_pending_proposal(session_factory)
    deliverer = ConsumerDeliverer()
    transport = ConsoleTransport(interactive=False)
    clock = ManualClock()

    with session_factory() as session:
        approval.accept_proposal(
            session, deliverer, transport, clock, proposal_id=proposal.id, decided_by="alice"
        )
        session.commit()

    with session_factory() as session:
        result = approval.reject_proposal(
            session, deliverer, clock, proposal_id=proposal.id, decided_by="bob", reason="too late"
        )

    assert result is None
    with session_factory() as session:
        row = get_proposal(session, proposal.id)
        assert row.status == "accepted"  # unchanged
        assert row.decided_by == "alice"


def test_notify_new_proposal_fans_out_on_proposal_pre_approval(session_factory) -> None:
    request, proposal = _seed_pending_proposal(session_factory)
    deliverer = ConsumerDeliverer()
    callbacks = RecordingCallbacks()
    deliverer.register_in_process("bi.assistant", callbacks)

    with session_factory() as session:
        approval.notify_new_proposal(session, deliverer, request.id, proposal)

    assert len(callbacks.proposals) == 1
    assert callbacks.proposals[0].id == proposal.id
