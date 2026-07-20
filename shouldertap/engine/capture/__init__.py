"""Spec §7.5/§8: reply/mute/skip parsing and the structuring pipeline. Graceful degradation to
structured=null on failure or low confidence -- an expert's answer is never dropped on the
floor, even if extraction fails entirely.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from apscheduler.schedulers.background import BackgroundScheduler
from sqlalchemy.orm import Session

from shouldertap.engine import approval, asker
from shouldertap.engine.clock import Clock
from shouldertap.engine.contracts import (
    KIND_FREEFORM_ANSWER,
    KIND_GLOSSARY_DEFINITION,
    ContextProposal,
    Provenance,
    TargetExpert,
)
from shouldertap.engine.delivery import ConsumerDeliverer
from shouldertap.engine.llm import LLMProvider
from shouldertap.engine.registry import RegistryConfig
from shouldertap.engine.scheduler.core import cancel_timer
from shouldertap.engine.store.mappers import proposal_to_row, row_to_request
from shouldertap.engine.store.models import RequestRow
from shouldertap.engine.store.repository import (
    adjust_open_asks,
    append_audit_event,
    get_consumer,
    get_expert,
    get_latest_proposal_for_request,
    get_request_by_thread_ref,
    list_active_timers_for_request,
    set_muted,
    set_request_status,
)
from shouldertap.engine.store.repository import (
    amend_proposal_answer as repo_amend_proposal_answer,
)
from shouldertap.engine.transports.base import Transport
from shouldertap.engine.transports.types import IncomingReply

_PROMPT_PATH = Path(__file__).parent / "prompts" / "structure_reply.md"

#: spec §8.2 -- the two built-in kind schemas. Consumers may register their own at
#: registration time; those take precedence over these when both apply to the same kind.
BUILTIN_KIND_SCHEMAS: dict[str, dict[str, Any]] = {
    KIND_GLOSSARY_DEFINITION: {
        "type": "object",
        "properties": {
            "definition": {"type": "string"},
            "caveats": {"type": "array", "items": {"type": "string"}},
            "examples": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["definition", "caveats"],
    },
    KIND_FREEFORM_ANSWER: {
        "type": "object",
        "properties": {
            "summary": {"type": "string"},
            "details": {"type": "string"},
        },
        "required": ["summary"],
    },
}

#: spec §8.2: "If structuring fails or confidence <0.3, still create the proposal with
#: structured=null."
STRUCTURE_CONFIDENCE_THRESHOLD = 0.3

CaptureKind = Literal["mute", "skip_rerouted", "skip_no_target", "answer", "amendment", "ignored"]


@dataclass
class CaptureOutcome:
    kind: CaptureKind
    request_id: str | None = None
    proposal_id: str | None = None
    ask_outcome: asker.AskOutcome | None = None


def resolve_kind_schema(
    kind: str, consumer_kind_schemas: dict[str, dict[str, Any]] | None
) -> dict[str, Any] | None:
    if consumer_kind_schemas and kind in consumer_kind_schemas:
        return consumer_kind_schemas[kind]
    return BUILTIN_KIND_SCHEMAS.get(kind)


def render_structure_prompt(*, answer: str, kind: str, schema: dict[str, Any]) -> str:
    template = _PROMPT_PATH.read_text()
    return template.format(answer=answer, kind=kind, schema=json.dumps(schema))


def structure_answer(
    llm_provider: LLMProvider | None,
    *,
    answer: str,
    kind: str,
    schema: dict[str, Any] | None,
) -> tuple[dict[str, Any] | None, float | None]:
    if llm_provider is None or schema is None:
        return None, None
    prompt = render_structure_prompt(answer=answer, kind=kind, schema=schema)
    structured, confidence = llm_provider.structure_answer(prompt)
    if structured is None:
        return None, confidence
    if confidence is not None and confidence < STRUCTURE_CONFIDENCE_THRESHOLD:
        # Keep the confidence value even though structured is nulled -- it's useful context
        # for the human approver reviewing the raw answer.
        return None, confidence
    return structured, confidence


def handle_reply(
    session: Session,
    scheduler: BackgroundScheduler,
    config: RegistryConfig,
    transport: Transport,
    llm_provider: LLMProvider | None,
    deliverer: ConsumerDeliverer,
    clock: Clock,
    reply: IncomingReply,
) -> CaptureOutcome:
    request_row = get_request_by_thread_ref(session, reply.thread_ref)
    if request_row is None:
        return CaptureOutcome(kind="ignored")

    actor = f"expert:{reply.expert_id}"
    normalized = reply.text.strip().lower()

    if normalized == "mute":
        set_muted(session, reply.expert_id, True)
        append_audit_event(
            session, ts=clock.now(), request_id=request_row.id, actor=actor, event="expert.muted"
        )
        return CaptureOutcome(kind="mute", request_id=request_row.id)

    if normalized == "skip":
        append_audit_event(
            session,
            ts=clock.now(),
            request_id=request_row.id,
            actor=actor,
            event="expert.skipped",
            detail={"declined_expert_id": reply.expert_id},
        )
        request = row_to_request(request_row)
        target_id = asker.resolve_escalation_target(session, config, request, reply.expert_id)
        adjust_open_asks(session, reply.expert_id, -1)
        if target_id is None:
            return CaptureOutcome(kind="skip_no_target", request_id=request_row.id)
        rerouted = request.model_copy(
            update={"target_experts": [TargetExpert(expert_id=target_id)]}
        )
        outcome = asker.route_and_ask(
            session, scheduler, config, transport, llm_provider, clock, rerouted
        )
        return CaptureOutcome(kind="skip_rerouted", request_id=request_row.id, ask_outcome=outcome)

    if len(reply.text.strip()) <= 2:
        return CaptureOutcome(kind="ignored", request_id=request_row.id)

    existing_proposal = get_latest_proposal_for_request(session, request_row.id)
    if existing_proposal is not None and existing_proposal.status != "pending":
        # spec §7.5: "ignored after decision (but logged)".
        append_audit_event(
            session,
            ts=clock.now(),
            request_id=request_row.id,
            actor=actor,
            event="reply.received",
            detail={"ignored": True, "reason": "already_decided"},
        )
        return CaptureOutcome(kind="ignored", request_id=request_row.id)

    if existing_proposal is not None:
        repo_amend_proposal_answer(session, existing_proposal.id, reply.text)
        append_audit_event(
            session,
            ts=clock.now(),
            request_id=request_row.id,
            actor=actor,
            event="reply.received",
            detail={"amendment": True, "proposal_id": existing_proposal.id},
        )
        return CaptureOutcome(
            kind="amendment", request_id=request_row.id, proposal_id=existing_proposal.id
        )

    return _capture_first_reply(
        session, scheduler, transport, llm_provider, deliverer, clock, reply, request_row
    )


def _capture_first_reply(
    session: Session,
    scheduler: BackgroundScheduler,
    transport: Transport,
    llm_provider: LLMProvider | None,
    deliverer: ConsumerDeliverer,
    clock: Clock,
    reply: IncomingReply,
    request_row: RequestRow,
) -> CaptureOutcome:
    actor = f"expert:{reply.expert_id}"
    append_audit_event(
        session, ts=clock.now(), request_id=request_row.id, actor=actor, event="reply.received"
    )

    consumer_row = get_consumer(session, request_row.consumer)
    consumer_schemas = consumer_row.kind_schemas if consumer_row is not None else None
    schema = resolve_kind_schema(request_row.kind, consumer_schemas)
    structured, confidence = structure_answer(
        llm_provider, answer=reply.text, kind=request_row.kind, schema=schema
    )

    expert = get_expert(session, reply.expert_id)
    provenance = Provenance(
        expert_id=reply.expert_id,
        expert_name=expert.name if expert is not None else reply.expert_id,
        answered_via=transport.name,
        slack_thread_ts=reply.thread_ref,
        answered_at=reply.received_at,
        escalated=request_row.escalated,
    )
    proposal = ContextProposal(
        request_id=request_row.id,
        kind=request_row.kind,
        answer=reply.text,
        structured=structured,
        confidence=confidence,
        provenance=provenance,
        consumer=request_row.consumer,
    )
    session.add(proposal_to_row(proposal, status="pending"))
    adjust_open_asks(session, reply.expert_id, -1)

    for timer in list_active_timers_for_request(session, request_row.id):
        cancel_timer(scheduler, session, timer.id)

    set_request_status(session, request_row.id, "proposed")
    append_audit_event(
        session,
        ts=clock.now(),
        request_id=request_row.id,
        actor="system",
        event="proposal.created",
        detail={"proposal_id": proposal.id},
    )
    transport.send_notification(
        expert_id=reply.expert_id, message="Got it — sending for review. Thank you!"
    )

    approval.notify_new_proposal(session, deliverer, request_row.id, proposal)
    if consumer_row is not None and consumer_row.auto_accept:
        # spec §9: auto_accept skips the human queue entirely -- still fully audited and the
        # expert still gets their attribution notification, via accept_proposal() itself.
        approval.accept_proposal(
            session, deliverer, transport, clock, proposal_id=proposal.id, decided_by="auto"
        )

    return CaptureOutcome(kind="answer", request_id=request_row.id, proposal_id=proposal.id)
