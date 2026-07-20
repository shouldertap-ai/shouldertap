"""Spec §9: accept/reject a pending proposal. On accept, fans `on_proposal_accepted` out to
every consumer subscribed to the request (§4.1 dedup fan-out) and notifies the expert with the
attribution promise made in the original ask (§7.2). `auto_accept=true` consumers (off by
default) skip the human queue entirely -- see capture.py, which calls accept_proposal() itself
right after creating the proposal when the owning consumer has that flag set.
"""

from __future__ import annotations

from collections.abc import Callable

from sqlalchemy.orm import Session

from shouldertap.engine.clock import Clock
from shouldertap.engine.contracts import ContextProposal
from shouldertap.engine.delivery import ConsumerDeliverer
from shouldertap.engine.store.mappers import row_to_proposal
from shouldertap.engine.store.models import ConsumerRow
from shouldertap.engine.store.repository import (
    append_audit_event,
    decide_proposal,
    get_consumer,
    get_proposal,
    get_request,
    mark_resolved,
    set_request_status,
)
from shouldertap.engine.transports.base import Transport


def accept_proposal(
    session: Session,
    deliverer: ConsumerDeliverer,
    transport: Transport,
    clock: Clock,
    *,
    proposal_id: str,
    decided_by: str,
    note: str | None = None,
) -> ContextProposal | None:
    row = get_proposal(session, proposal_id)
    if row is None or row.status != "pending":
        return None

    now = clock.now()
    decide_proposal(
        session, proposal_id, status="accepted", decided_by=decided_by, decided_at=now, note=note
    )
    mark_resolved(session, row.request_id, proposal_id, now)
    append_audit_event(
        session,
        ts=now,
        request_id=row.request_id,
        actor=f"approver:{decided_by}",
        event="proposal.accepted",
        detail={"proposal_id": proposal_id},
    )

    proposal = row_to_proposal(row)
    _fan_out(
        session, row.request_id, lambda consumer: deliverer.deliver_accepted(consumer, proposal)
    )
    _notify_expert_attribution(transport, proposal)
    return proposal


def reject_proposal(
    session: Session,
    deliverer: ConsumerDeliverer,
    clock: Clock,
    *,
    proposal_id: str,
    decided_by: str,
    reason: str,
) -> ContextProposal | None:
    row = get_proposal(session, proposal_id)
    if row is None or row.status != "pending":
        return None

    now = clock.now()
    decide_proposal(
        session, proposal_id, status="rejected", decided_by=decided_by, decided_at=now, note=reason
    )
    set_request_status(session, row.request_id, "rejected")
    append_audit_event(
        session,
        ts=now,
        request_id=row.request_id,
        actor=f"approver:{decided_by}",
        event="proposal.rejected",
        detail={"proposal_id": proposal_id, "reason": reason},
    )

    proposal = row_to_proposal(row)
    _fan_out(
        session,
        row.request_id,
        lambda consumer: deliverer.deliver_rejected(consumer, proposal, reason),
    )
    return proposal


def notify_new_proposal(
    session: Session, deliverer: ConsumerDeliverer, request_id: str, proposal: ContextProposal
) -> None:
    """Spec §4.3: on_proposal fires pre-approval, informationally, to every subscriber."""
    _fan_out(session, request_id, lambda consumer: deliverer.deliver_proposal(consumer, proposal))


def _fan_out(
    session: Session,
    request_id: str,
    deliver: Callable[[ConsumerRow], None],
) -> None:
    request_row = get_request(session, request_id)
    if request_row is None:
        return
    for consumer_id in request_row.subscribers:
        consumer_row = get_consumer(session, consumer_id)
        if consumer_row is not None:
            deliver(consumer_row)


def _notify_expert_attribution(transport: Transport, proposal: ContextProposal) -> None:
    transport.send_notification(
        expert_id=proposal.provenance.expert_id,
        message=(
            f"Your answer was accepted and recorded with your name as the source. Thank you, "
            f"{proposal.provenance.expert_name}!"
        ),
    )
