"""initial schema: requests, proposals, experts, consumers, audit_events, timers

Revision ID: 0001
Revises:
Create Date: 2026-07-20

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0001"
down_revision: str | None = None
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "requests",
        sa.Column("id", sa.String, primary_key=True),
        sa.Column("org_id", sa.String, nullable=False, server_default="default"),
        sa.Column("kind", sa.String, nullable=False),
        sa.Column("topic", sa.String, nullable=False),
        sa.Column("question", sa.String, nullable=False),
        sa.Column("context", sa.JSON, nullable=False),
        sa.Column("target_experts", sa.JSON, nullable=True),
        sa.Column("routing_policy", sa.JSON, nullable=False),
        sa.Column("consumer", sa.String, nullable=False),
        sa.Column("dedup_key", sa.String, nullable=True),
        sa.Column("correlation", sa.JSON, nullable=True),
        sa.Column("subscribers", sa.JSON, nullable=False),
        sa.Column("status", sa.String, nullable=False, server_default="queued"),
        sa.Column("asked_expert_id", sa.String, nullable=True),
        sa.Column("asked_at", sa.DateTime, nullable=True),
        sa.Column("thread_ref", sa.String, nullable=True),
        sa.Column("escalated", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime, nullable=False),
        sa.Column("resolved_at", sa.DateTime, nullable=True),
        sa.Column("resolved_proposal_id", sa.String, nullable=True),
        sa.Column("failure_reason", sa.JSON, nullable=True),
    )
    op.create_index("ix_requests_dedup_key", "requests", ["dedup_key"])
    op.create_index("ix_requests_status", "requests", ["status"])
    op.create_index("ix_requests_thread_ref", "requests", ["thread_ref"])

    op.create_table(
        "proposals",
        sa.Column("id", sa.String, primary_key=True),
        sa.Column("request_id", sa.String, sa.ForeignKey("requests.id"), nullable=False),
        sa.Column("kind", sa.String, nullable=False),
        sa.Column("answer", sa.String, nullable=False),
        sa.Column("structured", sa.JSON, nullable=True),
        sa.Column("provenance", sa.JSON, nullable=False),
        sa.Column("confidence", sa.Float, nullable=True),
        sa.Column("consumer", sa.String, nullable=False),
        sa.Column("status", sa.String, nullable=False, server_default="pending"),
        sa.Column("decided_by", sa.String, nullable=True),
        sa.Column("decided_at", sa.DateTime, nullable=True),
        sa.Column("decision_note", sa.String, nullable=True),
        sa.Column("created_at", sa.DateTime, nullable=False),
    )
    op.create_index("ix_proposals_request_id", "proposals", ["request_id"])
    op.create_index("ix_proposals_status", "proposals", ["status"])

    op.create_table(
        "experts",
        sa.Column("id", sa.String, primary_key=True),
        sa.Column("name", sa.String, nullable=False),
        sa.Column("topics", sa.JSON, nullable=False),
        sa.Column("escalation_to", sa.String, nullable=True),
        sa.Column("muted", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("open_asks", sa.Integer, nullable=False, server_default="0"),
        sa.Column("asks_today", sa.Integer, nullable=False, server_default="0"),
        sa.Column("asks_today_date", sa.String, nullable=True),
    )

    op.create_table(
        "consumers",
        sa.Column("id", sa.String, primary_key=True),
        sa.Column("handles_kinds", sa.JSON, nullable=False),
        sa.Column("delivery_type", sa.String, nullable=False),
        sa.Column("webhook_url", sa.String, nullable=True),
        sa.Column("dedup_window_seconds", sa.Integer, nullable=False, server_default="86400"),
        sa.Column("auto_accept", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("kind_schemas", sa.JSON, nullable=True),
    )

    op.create_table(
        "audit_events",
        sa.Column("id", sa.String, primary_key=True),
        sa.Column("ts", sa.DateTime, nullable=False),
        sa.Column("request_id", sa.String, nullable=True),
        sa.Column("actor", sa.String, nullable=False),
        sa.Column("event", sa.String, nullable=False),
        sa.Column("detail", sa.JSON, nullable=True),
    )
    op.create_index("ix_audit_events_request_id", "audit_events", ["request_id"])

    op.create_table(
        "timers",
        sa.Column("id", sa.String, primary_key=True),
        sa.Column("request_id", sa.String, sa.ForeignKey("requests.id"), nullable=False),
        sa.Column("job_type", sa.String, nullable=False),
        sa.Column("fire_at", sa.DateTime, nullable=False),
        sa.Column("apscheduler_job_id", sa.String, nullable=True),
        sa.Column("status", sa.String, nullable=False, server_default="scheduled"),
    )
    op.create_index("ix_timers_request_id", "timers", ["request_id"])


def downgrade() -> None:
    op.drop_table("timers")
    op.drop_table("audit_events")
    op.drop_table("consumers")
    op.drop_table("experts")
    op.drop_table("proposals")
    op.drop_table("requests")
