"""The single in-process entry point wiring contracts/store/router/scheduler/llm/transports/
asker/capture/approval together (build plan). Used directly by tests and by server/app.py --
the only process that touches the DB directly; CLI and MCP reach it exclusively over HTTP.

Also implements scheduler.jobs.JobContext (structurally, via duck typing): this is where
escalation/give-up timers and the quiet-hours sweep actually do their work when they fire,
including the "re-derive eligibility from stored state vs. the clock" behavior the build plan
calls for, rather than trusting a timer's own invocation timing.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import Literal

from apscheduler.schedulers.background import BackgroundScheduler
from sqlalchemy.orm import Session, sessionmaker

from shouldertap.engine import approval, asker, capture
from shouldertap.engine.clock import Clock
from shouldertap.engine.contracts import (
    ContextProposal,
    ContextRequest,
    Reason,
    ReasonCode,
    TargetExpert,
)
from shouldertap.engine.delivery import ConsumerDeliverer
from shouldertap.engine.llm import LLMProvider
from shouldertap.engine.registry import RegistryConfig
from shouldertap.engine.scheduler import jobs
from shouldertap.engine.scheduler.core import schedule_give_up
from shouldertap.engine.store.mappers import request_to_row, row_to_proposal, row_to_request
from shouldertap.engine.store.repository import (
    add_subscriber,
    adjust_open_asks,
    append_audit_event,
    find_open_dedup_match,
    find_resolved_dedup_match,
    get_consumer,
    get_proposal,
    get_request,
    list_queued_requests,
    mark_escalated,
    mark_failed,
)
from shouldertap.engine.transports.base import Transport
from shouldertap.engine.transports.types import IncomingReply

_TERMINAL_STATUSES = frozenset({"proposed", "accepted", "rejected", "failed"})

SubmitStatus = Literal["queued", "deduped_resolved", "deduped_open", "failed"]


@dataclass
class SubmitOutcome:
    status: SubmitStatus
    request_id: str
    proposal: ContextProposal | None = None


@dataclass
class Facade:
    session_factory: sessionmaker[Session]
    scheduler: BackgroundScheduler
    config: RegistryConfig
    transport: Transport
    llm_provider: LLMProvider | None
    deliverer: ConsumerDeliverer
    clock: Clock

    def __post_init__(self) -> None:
        jobs.set_context(self)
        self.transport.register_reply_handler(self._on_incoming_reply)

    # --- consumer-facing entry point ---

    def submit_request(self, request: ContextRequest) -> SubmitOutcome:
        with self.session_factory() as session:
            if request.dedup_key:
                consumer_row = get_consumer(session, request.consumer)
                dedup_window = (
                    timedelta(seconds=consumer_row.dedup_window_seconds)
                    if consumer_row is not None
                    else timedelta(hours=24)
                )
                since = self.clock.now() - dedup_window

                resolved = find_resolved_dedup_match(
                    session, org_id=request.org_id, dedup_key=request.dedup_key, since=since
                )
                if resolved is not None and resolved.resolved_proposal_id is not None:
                    add_subscriber(session, resolved.id, request.consumer)
                    append_audit_event(
                        session,
                        ts=self.clock.now(),
                        request_id=resolved.id,
                        actor=f"consumer:{request.consumer}",
                        event="request.deduped",
                        detail={"resolved": True, "original_request_id": resolved.id},
                    )
                    proposal_row = get_proposal(session, resolved.resolved_proposal_id)
                    proposal = row_to_proposal(proposal_row) if proposal_row is not None else None
                    if proposal is not None and consumer_row is not None:
                        self.deliverer.deliver_accepted(consumer_row, proposal)
                    session.commit()
                    return SubmitOutcome(
                        status="deduped_resolved", request_id=resolved.id, proposal=proposal
                    )

                open_match = find_open_dedup_match(
                    session, org_id=request.org_id, dedup_key=request.dedup_key, since=since
                )
                if open_match is not None:
                    add_subscriber(session, open_match.id, request.consumer)
                    append_audit_event(
                        session,
                        ts=self.clock.now(),
                        request_id=open_match.id,
                        actor=f"consumer:{request.consumer}",
                        event="request.deduped",
                        detail={"resolved": False, "original_request_id": open_match.id},
                    )
                    session.commit()
                    return SubmitOutcome(status="deduped_open", request_id=open_match.id)

            session.add(request_to_row(request))
            append_audit_event(
                session,
                ts=self.clock.now(),
                request_id=request.id,
                actor=f"consumer:{request.consumer}",
                event="request.received",
            )
            give_up_after = (
                request.routing_policy.give_up_after or self.config.defaults.give_up_after
            )
            schedule_give_up(
                self.scheduler,
                session,
                request_id=request.id,
                fire_at=self.clock.now() + give_up_after,
            )
            session.commit()

            outcome = asker.route_and_ask(
                session,
                self.scheduler,
                self.config,
                self.transport,
                self.llm_provider,
                self.clock,
                request,
            )
            status: SubmitStatus = "queued"
            if outcome.status == "no_expert_found":
                self._fail_request(session, request.id, ReasonCode.NO_EXPERT_FOUND)
                status = "failed"
            session.commit()

        return SubmitOutcome(status=status, request_id=request.id)

    # --- approval entry points (used by server/CLI) ---

    def accept_proposal(
        self, *, proposal_id: str, decided_by: str, note: str | None = None
    ) -> ContextProposal | None:
        with self.session_factory() as session:
            result = approval.accept_proposal(
                session,
                self.deliverer,
                self.transport,
                self.clock,
                proposal_id=proposal_id,
                decided_by=decided_by,
                note=note,
            )
            session.commit()
        return result

    def reject_proposal(
        self, *, proposal_id: str, decided_by: str, reason: str
    ) -> ContextProposal | None:
        with self.session_factory() as session:
            result = approval.reject_proposal(
                session,
                self.deliverer,
                self.clock,
                proposal_id=proposal_id,
                decided_by=decided_by,
                reason=reason,
            )
            session.commit()
        return result

    # --- reply capture (wired to the transport at construction time) ---

    def _on_incoming_reply(self, reply: IncomingReply) -> None:
        with self.session_factory() as session:
            outcome = capture.handle_reply(
                session,
                self.scheduler,
                self.config,
                self.transport,
                self.llm_provider,
                self.deliverer,
                self.clock,
                reply,
            )
            if outcome.kind == "skip_no_target" and outcome.request_id is not None:
                # spec's reason codes distinguish "declined, nowhere to escalate" from a plain
                # timeout -- fail it now rather than waiting for give-up to time out with the
                # wrong reason.
                self._fail_request(session, outcome.request_id, ReasonCode.EXPERT_DECLINED)
            session.commit()

    # --- scheduler.jobs.JobContext implementation ---

    def handle_escalation_timer(self, request_id: str) -> None:
        with self.session_factory() as session:
            request_row = get_request(session, request_id)
            if request_row is None or request_row.status in _TERMINAL_STATUSES:
                return
            if request_row.asked_at is None:
                return
            request = row_to_request(request_row)
            escalation_after = (
                request.routing_policy.escalation_after or self.config.defaults.escalation_after
            )
            if self.clock.now() < request_row.asked_at + escalation_after:
                return  # stale/early fire (e.g. superseded by a later ask); nothing to do

            previous_expert_id = request_row.asked_expert_id
            target_id = (
                asker.resolve_escalation_target(session, self.config, request, previous_expert_id)
                if previous_expert_id
                else None
            )
            append_audit_event(
                session,
                ts=self.clock.now(),
                request_id=request_id,
                actor="system",
                event="escalation.fired",
                detail={"from_expert_id": previous_expert_id, "to_expert_id": target_id},
            )
            if previous_expert_id:
                adjust_open_asks(session, previous_expert_id, -1)
            if target_id is None:
                session.commit()
                return  # nothing to escalate to; the give_up timer remains the backstop

            mark_escalated(session, request_id)
            rerouted = request.model_copy(
                update={"target_experts": [TargetExpert(expert_id=target_id)]}
            )
            asker.route_and_ask(
                session,
                self.scheduler,
                self.config,
                self.transport,
                self.llm_provider,
                self.clock,
                rerouted,
            )
            session.commit()

    def handle_give_up_timer(self, request_id: str) -> None:
        with self.session_factory() as session:
            request_row = get_request(session, request_id)
            if request_row is None or request_row.status in _TERMINAL_STATUSES:
                return
            # spec §7.4: an expert who was actually asked and never replied is a timeout; a
            # request that was held the whole time (all candidates capped) gets rate_limited.
            reason_code = (
                ReasonCode.TIMEOUT if request_row.asked_at is not None else ReasonCode.RATE_LIMITED
            )
            self._fail_request(session, request_id, reason_code)
            session.commit()

    def run_quiet_hours_sweep(self) -> None:
        with self.session_factory() as session:
            for row in list_queued_requests(session):
                request = row_to_request(row)
                asker.route_and_ask(
                    session,
                    self.scheduler,
                    self.config,
                    self.transport,
                    self.llm_provider,
                    self.clock,
                    request,
                )
            session.commit()

    # --- internal ---

    def _fail_request(self, session: Session, request_id: str, reason_code: ReasonCode) -> None:
        reason = Reason(code=reason_code)
        mark_failed(session, request_id, reason.model_dump(mode="json"))
        append_audit_event(
            session,
            ts=self.clock.now(),
            request_id=request_id,
            actor="system",
            event="request.failed",
            detail={"reason": reason_code.value},
        )
        request_row = get_request(session, request_id)
        if request_row is None:
            return
        for consumer_id in request_row.subscribers:
            consumer_row = get_consumer(session, consumer_id)
            if consumer_row is not None:
                self.deliverer.deliver_request_failed(consumer_row, request_id, reason)
