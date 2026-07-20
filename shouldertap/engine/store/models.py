"""SQLAlchemy 2.0 ORM models for exactly the 6 tables spec §10.1 names. `requests` carries a
`subscribers` JSON column for dedup fan-out (build-plan decision -- keeps the table count at 6
rather than adding a 7th join table for subscriptions).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, ForeignKey, Index
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class RequestRow(Base):
    __tablename__ = "requests"

    id: Mapped[str] = mapped_column(primary_key=True)
    org_id: Mapped[str] = mapped_column(default="default")
    kind: Mapped[str]
    topic: Mapped[str]
    question: Mapped[str]
    context: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    target_experts: Mapped[list[dict[str, Any]] | None] = mapped_column(JSON, nullable=True)
    routing_policy: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    consumer: Mapped[str]
    dedup_key: Mapped[str | None] = mapped_column(nullable=True)
    correlation: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    subscribers: Mapped[list[str]] = mapped_column(JSON, default=list)
    status: Mapped[str] = mapped_column(default="queued")
    asked_expert_id: Mapped[str | None] = mapped_column(nullable=True)
    asked_at: Mapped[datetime | None] = mapped_column(nullable=True)
    thread_ref: Mapped[str | None] = mapped_column(nullable=True)
    escalated: Mapped[bool] = mapped_column(default=False)
    created_at: Mapped[datetime]
    resolved_at: Mapped[datetime | None] = mapped_column(nullable=True)
    resolved_proposal_id: Mapped[str | None] = mapped_column(nullable=True)
    failure_reason: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)

    __table_args__ = (
        Index("ix_requests_dedup_key", "dedup_key"),
        Index("ix_requests_status", "status"),
        Index("ix_requests_thread_ref", "thread_ref"),
    )


class ProposalRow(Base):
    __tablename__ = "proposals"

    id: Mapped[str] = mapped_column(primary_key=True)
    request_id: Mapped[str] = mapped_column(ForeignKey("requests.id"))
    kind: Mapped[str]
    answer: Mapped[str]
    structured: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    provenance: Mapped[dict[str, Any]] = mapped_column(JSON)
    confidence: Mapped[float | None] = mapped_column(nullable=True)
    consumer: Mapped[str]
    status: Mapped[str] = mapped_column(default="pending")
    decided_by: Mapped[str | None] = mapped_column(nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(nullable=True)
    decision_note: Mapped[str | None] = mapped_column(nullable=True)
    created_at: Mapped[datetime]

    __table_args__ = (
        Index("ix_proposals_request_id", "request_id"),
        Index("ix_proposals_status", "status"),
    )


class ExpertRow(Base):
    __tablename__ = "experts"

    id: Mapped[str] = mapped_column(primary_key=True)
    name: Mapped[str]
    topics: Mapped[list[str]] = mapped_column(JSON, default=list)
    escalation_to: Mapped[str | None] = mapped_column(nullable=True)
    muted: Mapped[bool] = mapped_column(default=False)
    open_asks: Mapped[int] = mapped_column(default=0)
    asks_today: Mapped[int] = mapped_column(default=0)
    asks_today_date: Mapped[str | None] = mapped_column(nullable=True)


class ConsumerRow(Base):
    __tablename__ = "consumers"

    id: Mapped[str] = mapped_column(primary_key=True)
    handles_kinds: Mapped[list[str]] = mapped_column(JSON, default=list)
    delivery_type: Mapped[str]
    webhook_url: Mapped[str | None] = mapped_column(nullable=True)
    dedup_window_seconds: Mapped[int] = mapped_column(default=86400)
    auto_accept: Mapped[bool] = mapped_column(default=False)
    kind_schemas: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)


class AuditEventRow(Base):
    __tablename__ = "audit_events"

    id: Mapped[str] = mapped_column(primary_key=True)
    ts: Mapped[datetime]
    request_id: Mapped[str | None] = mapped_column(nullable=True)
    actor: Mapped[str]
    event: Mapped[str]
    detail: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)

    __table_args__ = (Index("ix_audit_events_request_id", "request_id"),)


class TimerRow(Base):
    __tablename__ = "timers"

    id: Mapped[str] = mapped_column(primary_key=True)
    request_id: Mapped[str] = mapped_column(ForeignKey("requests.id"))
    job_type: Mapped[str]
    fire_at: Mapped[datetime]
    apscheduler_job_id: Mapped[str | None] = mapped_column(nullable=True)
    status: Mapped[str] = mapped_column(default="scheduled")

    __table_args__ = (Index("ix_timers_request_id", "request_id"),)
