"""Query/CRUD functions used by router/asker/capture/approval/scheduler/API. Operates on ORM
rows; callers map to/from Pydantic contracts via mappers.py as needed. Grows with each engine
module rather than being fully speculated up front.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from shouldertap.engine.contracts import ConsumerRegistration
from shouldertap.engine.ids import new_id
from shouldertap.engine.store.models import (
    AuditEventRow,
    ConsumerRow,
    ExpertRow,
    ProposalRow,
    RequestRow,
    TimerRow,
)

# --- requests ---


def get_request(session: Session, request_id: str) -> RequestRow | None:
    return session.get(RequestRow, request_id)


def find_open_dedup_match(
    session: Session, *, org_id: str, dedup_key: str, since: datetime
) -> RequestRow | None:
    """An in-flight (not yet resolved/failed) request with the same dedup_key, still within
    the dedup window -- spec §4.1: attach the new consumer as a subscriber, don't re-ask.
    """
    stmt = (
        select(RequestRow)
        .where(RequestRow.org_id == org_id)
        .where(RequestRow.dedup_key == dedup_key)
        .where(RequestRow.created_at >= since)
        .where(RequestRow.status.in_(["queued", "asked", "escalated"]))
        .order_by(RequestRow.created_at.desc())
    )
    return session.execute(stmt).scalars().first()


def find_resolved_dedup_match(
    session: Session, *, org_id: str, dedup_key: str, since: datetime
) -> RequestRow | None:
    """An accepted request with the same dedup_key, resolved within the dedup window -- spec
    §4.1: deliver the existing accepted proposal to the new consumer immediately.
    """
    stmt = (
        select(RequestRow)
        .where(RequestRow.org_id == org_id)
        .where(RequestRow.dedup_key == dedup_key)
        .where(RequestRow.status == "accepted")
        .where(RequestRow.resolved_at.is_not(None))
        .where(RequestRow.resolved_at >= since)
        .order_by(RequestRow.resolved_at.desc())
    )
    return session.execute(stmt).scalars().first()


def add_subscriber(session: Session, request_id: str, consumer_id: str) -> None:
    row = session.get(RequestRow, request_id)
    if row is None:
        return
    if consumer_id not in row.subscribers:
        row.subscribers = [*row.subscribers, consumer_id]


def set_request_status(session: Session, request_id: str, status: str) -> None:
    row = session.get(RequestRow, request_id)
    if row is not None:
        row.status = status


def list_queued_requests(session: Session) -> list[RequestRow]:
    """Used by the quiet-hours sweep: everything still waiting for a first successful
    delivery, whether held by quiet hours or by all-candidates-capped.
    """
    stmt = select(RequestRow).where(RequestRow.status == "queued")
    return list(session.execute(stmt).scalars().all())


def set_asked(
    session: Session,
    request_id: str,
    *,
    expert_id: str,
    asked_at: datetime,
    thread_ref: str | None = None,
) -> None:
    row = session.get(RequestRow, request_id)
    if row is None:
        return
    row.asked_expert_id = expert_id
    row.asked_at = asked_at
    row.thread_ref = thread_ref
    row.status = "asked"


def get_request_by_thread_ref(session: Session, thread_ref: str) -> RequestRow | None:
    stmt = select(RequestRow).where(RequestRow.thread_ref == thread_ref)
    return session.execute(stmt).scalars().first()


def mark_escalated(session: Session, request_id: str) -> None:
    """Sets the `escalated` flag only, deliberately not `status` -- the redelivery that follows
    an escalation immediately calls set_asked() again, which would otherwise overwrite an
    "escalated" status value right back to "asked" a moment later. `escalated` is the durable,
    always-accurate signal (it's also what Provenance.escalated mirrors); `status` continues to
    just mean "someone currently has an open ask out for this," whether original or escalated.
    """
    row = session.get(RequestRow, request_id)
    if row is not None:
        row.escalated = True


def mark_resolved(
    session: Session, request_id: str, proposal_id: str, resolved_at: datetime
) -> None:
    row = session.get(RequestRow, request_id)
    if row is None:
        return
    row.status = "accepted"
    row.resolved_proposal_id = proposal_id
    row.resolved_at = resolved_at


def mark_failed(session: Session, request_id: str, reason: dict[str, Any]) -> None:
    row = session.get(RequestRow, request_id)
    if row is None:
        return
    row.status = "failed"
    row.failure_reason = reason


# --- proposals ---


def get_proposal(session: Session, proposal_id: str) -> ProposalRow | None:
    return session.get(ProposalRow, proposal_id)


def list_pending_proposals(session: Session) -> list[ProposalRow]:
    stmt = select(ProposalRow).where(ProposalRow.status == "pending")
    return list(session.execute(stmt).scalars().all())


def get_latest_proposal_for_request(session: Session, request_id: str) -> ProposalRow | None:
    stmt = (
        select(ProposalRow)
        .where(ProposalRow.request_id == request_id)
        .order_by(ProposalRow.created_at.desc())
    )
    return session.execute(stmt).scalars().first()


def decide_proposal(
    session: Session,
    proposal_id: str,
    *,
    status: str,
    decided_by: str,
    decided_at: datetime,
    note: str | None = None,
) -> ProposalRow | None:
    row = session.get(ProposalRow, proposal_id)
    if row is None:
        return None
    row.status = status
    row.decided_by = decided_by
    row.decided_at = decided_at
    row.decision_note = note
    return row


def amend_proposal_answer(session: Session, proposal_id: str, addendum: str) -> None:
    row = session.get(ProposalRow, proposal_id)
    if row is not None and row.status == "pending":
        row.answer = f"{row.answer}\n\n[amendment] {addendum}"


# --- experts ---


def get_expert(session: Session, expert_id: str) -> ExpertRow | None:
    return session.get(ExpertRow, expert_id)


def upsert_expert(
    session: Session, *, expert_id: str, name: str, topics: list[str], escalation_to: str | None
) -> ExpertRow:
    row = session.get(ExpertRow, expert_id)
    if row is None:
        row = ExpertRow(id=expert_id, name=name, topics=topics, escalation_to=escalation_to)
        session.add(row)
    else:
        row.name = name
        row.topics = topics
        row.escalation_to = escalation_to
    return row


def _normalize_topic(text: str) -> str:
    """Duplicates router.normalize()'s one-line rule rather than importing it -- router.py
    already imports from this module, so importing back would be circular. Kept in sync by
    inspection; both apply the same lowercase-and-collapse-whitespace rule.
    """
    return " ".join(text.lower().split())


def list_experts_for_topic(session: Session, normalized_topic: str) -> list[ExpertRow]:
    """spec §6 step 2: exact-match against expert `topics`. `normalized_topic` (the caller's
    already-normalized query topic) must be compared against equally-normalized stored expert
    topics -- comparing against raw, as-typed-in-YAML topic strings would silently miss exact
    matches that differ only in case/whitespace (e.g. an admin typing "Revenue  Metrics"),
    falling through to the fuzzy-match step and mislabeling the routing reason.
    """
    experts = session.execute(select(ExpertRow)).scalars().all()
    return [e for e in experts if normalized_topic in {_normalize_topic(t) for t in e.topics}]


def list_all_experts(session: Session) -> list[ExpertRow]:
    return list(session.execute(select(ExpertRow)).scalars().all())


def replace_experts(
    session: Session,
    experts: list[tuple[str, str, list[str], str | None]],
) -> None:
    """Full replace semantics for `PUT /experts` (spec §5): each tuple is
    (id, name, topics, escalation_to). Experts no longer present are removed outright rather
    than left as stale, still-routable DB rows.
    """
    keep_ids = {expert_id for expert_id, _, _, _ in experts}
    for row in list_all_experts(session):
        if row.id not in keep_ids:
            session.delete(row)
    for expert_id, name, topics, escalation_to in experts:
        upsert_expert(
            session, expert_id=expert_id, name=name, topics=topics, escalation_to=escalation_to
        )


def set_muted(session: Session, expert_id: str, muted: bool) -> None:
    row = session.get(ExpertRow, expert_id)
    if row is not None:
        row.muted = muted


def adjust_open_asks(session: Session, expert_id: str, delta: int) -> None:
    row = session.get(ExpertRow, expert_id)
    if row is not None:
        row.open_asks = max(0, row.open_asks + delta)


def record_ask_today(session: Session, expert_id: str, today: str) -> None:
    row = session.get(ExpertRow, expert_id)
    if row is None:
        return
    if row.asks_today_date != today:
        row.asks_today_date = today
        row.asks_today = 0
    row.asks_today += 1


# --- consumers ---


def get_consumer(session: Session, consumer_id: str) -> ConsumerRow | None:
    return session.get(ConsumerRow, consumer_id)


def upsert_consumer(session: Session, registration: ConsumerRegistration) -> ConsumerRow:
    row = session.get(ConsumerRow, registration.id)
    webhook_url = registration.delivery.url if registration.delivery.type == "webhook" else None
    if row is None:
        row = ConsumerRow(
            id=registration.id,
            handles_kinds=registration.handles_kinds,
            delivery_type=registration.delivery.type,
            webhook_url=webhook_url,
            dedup_window_seconds=int(registration.dedup_window.total_seconds()),
            auto_accept=registration.auto_accept,
            kind_schemas=registration.kind_schemas or None,
        )
        session.add(row)
    else:
        row.handles_kinds = registration.handles_kinds
        row.delivery_type = registration.delivery.type
        row.webhook_url = webhook_url
        row.dedup_window_seconds = int(registration.dedup_window.total_seconds())
        row.auto_accept = registration.auto_accept
        row.kind_schemas = registration.kind_schemas or None
    return row


def delete_consumer(session: Session, consumer_id: str) -> None:
    row = session.get(ConsumerRow, consumer_id)
    if row is not None:
        session.delete(row)


# --- audit ---


def append_audit_event(
    session: Session,
    *,
    ts: datetime,
    request_id: str | None,
    actor: str,
    event: str,
    detail: dict[str, Any] | None = None,
) -> AuditEventRow:
    row = AuditEventRow(
        id=new_id("audit"), ts=ts, request_id=request_id, actor=actor, event=event, detail=detail
    )
    session.add(row)
    return row


def list_audit_events(session: Session, request_id: str) -> list[AuditEventRow]:
    stmt = (
        select(AuditEventRow)
        .where(AuditEventRow.request_id == request_id)
        .order_by(AuditEventRow.ts.asc())
    )
    return list(session.execute(stmt).scalars().all())


# --- timers ---


def create_timer(
    session: Session,
    *,
    request_id: str,
    job_type: str,
    fire_at: datetime,
    apscheduler_job_id: str | None = None,
) -> TimerRow:
    row = TimerRow(
        id=new_id("timer"),
        request_id=request_id,
        job_type=job_type,
        fire_at=fire_at,
        apscheduler_job_id=apscheduler_job_id,
        status="scheduled",
    )
    session.add(row)
    return row


def get_timer(session: Session, timer_id: str) -> TimerRow | None:
    return session.get(TimerRow, timer_id)


def list_active_timers_for_request(session: Session, request_id: str) -> list[TimerRow]:
    stmt = (
        select(TimerRow)
        .where(TimerRow.request_id == request_id)
        .where(TimerRow.status == "scheduled")
    )
    return list(session.execute(stmt).scalars().all())


def update_timer_status(session: Session, timer_id: str, status: str) -> None:
    row = session.get(TimerRow, timer_id)
    if row is not None:
        row.status = status
