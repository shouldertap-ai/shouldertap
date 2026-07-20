"""Spec §7: composes the mandatory 6-element ask message (§7.2), applies quiet-hours and
rate-limit checks before sending (§7.4), and schedules the escalation timer once a request is
actually delivered.

give_up_after is intentionally NOT scheduled here -- see facade.py, which schedules it once at
request submission as a single backstop deadline covering the whole "hold in queue until
capacity or give-up" lifecycle (§7.4), independent of how many routing/capacity retries happen
along the way. escalation_after, by contrast, is only meaningful once a request has actually
been asked of someone, so it's scheduled here at the point of delivery.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime, time
from pathlib import Path
from typing import Literal
from zoneinfo import ZoneInfo

from apscheduler.schedulers.background import BackgroundScheduler
from sqlalchemy.orm import Session

from shouldertap.engine import router
from shouldertap.engine.clock import Clock
from shouldertap.engine.contracts import ContextRequest
from shouldertap.engine.llm import LLMProvider
from shouldertap.engine.registry import DefaultsConfig, RegistryConfig
from shouldertap.engine.scheduler.core import schedule_escalation
from shouldertap.engine.store.models import ExpertRow
from shouldertap.engine.store.repository import (
    adjust_open_asks,
    append_audit_event,
    get_expert,
    record_ask_today,
    set_asked,
)
from shouldertap.engine.transports.base import Transport

_PROMPT_PATH = Path(__file__).parent / "prompts" / "draft_question.md"

AskStatus = Literal["asked", "queued_quiet_hours", "rate_limited_hold", "no_expert_found"]


@dataclass
class AskOutcome:
    status: AskStatus
    expert_id: str | None = None
    thread_ref: str | None = None


def is_within_quiet_hours(
    now_utc: datetime, quiet_hours: tuple[time, time] | None, org_timezone: str
) -> bool:
    if quiet_hours is None:
        return False
    local_now = now_utc.replace(tzinfo=UTC).astimezone(ZoneInfo(org_timezone)).time()
    start, end = quiet_hours
    if start <= end:
        return start <= local_now < end
    return local_now >= start or local_now < end  # window crosses midnight


def today_str(clock: Clock) -> str:
    return clock.now().date().isoformat()


def is_capped(expert: ExpertRow, defaults: DefaultsConfig, today: str) -> bool:
    """spec §7.4: caps are checked before sending. `asks_today` only counts if it was last
    recorded today -- an unresolved rollover from a previous day must not count as saturated.
    """
    if expert.open_asks >= defaults.max_open_asks_per_expert:
        return True
    effective_asks_today = expert.asks_today if expert.asks_today_date == today else 0
    return effective_asks_today >= defaults.max_asks_per_expert_per_day


def render_draft_question_prompt(
    *, question: str, context: dict[str, object], kind: str, expert_name: str
) -> str:
    template = _PROMPT_PATH.read_text()
    return template.format(
        question=question, context=json.dumps(context), kind=kind, expert_name=expert_name
    )


def draft_question_text(
    llm_provider: LLMProvider | None, *, request: ContextRequest, expert_name: str
) -> str:
    """spec §7.3: graceful fallback to the raw question string verbatim if no LLM is configured
    or the call fails -- this is what makes zero-LLM degradation (acceptance criterion 7) work
    with no special-casing here.
    """
    if llm_provider is None:
        return request.question
    prompt = render_draft_question_prompt(
        question=request.question,
        context=request.context,
        kind=request.kind,
        expert_name=expert_name,
    )
    drafted = llm_provider.draft_question(prompt)
    return drafted if drafted else request.question


def compose_message(*, request: ContextRequest, expert_name: str, question_text: str) -> str:
    """spec §7.2: the six mandatory elements, in this exact order. Every outbound ask contains
    self-identification and an opt-out by construction -- there is no code path that omits them.
    """
    asked_because = request.context.get("asked_because") or (
        f"{request.consumer} hit a question it can't answer without you"
    )
    return "\n\n".join(
        [
            "\U0001f91d I'm ShoulderTap, an automated assistant.",
            f"{request.consumer} needs your help: {asked_because}",
            question_text,
            "A one-or-two sentence reply here is all that's needed.",
            "Your answer will be recorded with your name as the source, after human review.",
            "Reply `mute` to stop receiving asks; reply `skip` to pass this one to someone else.",
        ]
    )


def resolve_escalation_target(
    session: Session, config: RegistryConfig, request: ContextRequest, previous_expert_id: str
) -> str | None:
    """Used by facade.py when an escalation timer fires. Prefers the request's own
    routing_policy.escalation_targets; falls back to the previously-asked expert's configured
    escalation_to.
    """
    if request.routing_policy.escalation_targets:
        for candidate_id in request.routing_policy.escalation_targets:
            row = get_expert(session, candidate_id)
            if row is not None and not row.muted:
                return candidate_id
        return None

    expert_config = config.expert_by_id(previous_expert_id)
    if expert_config and expert_config.escalation_to:
        target_row = get_expert(session, expert_config.escalation_to)
        if target_row is not None and not target_row.muted:
            return expert_config.escalation_to
    return None


def route_and_ask(
    session: Session,
    scheduler: BackgroundScheduler,
    config: RegistryConfig,
    transport: Transport,
    llm_provider: LLMProvider | None,
    clock: Clock,
    request: ContextRequest,
) -> AskOutcome:
    """Resolves a candidate expert (retrying past anyone at their rate-limit cap), checks quiet
    hours, and -- if clear to send -- delivers the message and schedules escalation.
    """
    exclude: frozenset[str] = frozenset()
    hit_any_capped = False
    today = today_str(clock)

    while True:
        result = router.resolve(session, config, request, exclude=exclude)
        append_audit_event(
            session,
            ts=clock.now(),
            request_id=request.id,
            actor="system",
            event="routing.resolved",
            detail={"expert_id": result.expert_id, "reason": result.reason},
        )
        if result.expert_id is None:
            if hit_any_capped:
                # spec §7.4: "if all capped, hold in queue until capacity or give-up" -- not
                # an immediate failure. The give_up timer scheduled at submission is the
                # backstop that eventually fails this with reason=rate_limited.
                return AskOutcome(status="rate_limited_hold")
            return AskOutcome(status="no_expert_found")

        expert = get_expert(session, result.expert_id)
        if expert is None:
            exclude = exclude | {result.expert_id}
            continue

        if is_capped(expert, config.defaults, today):
            hit_any_capped = True
            append_audit_event(
                session,
                ts=clock.now(),
                request_id=request.id,
                actor="system",
                event="ask.rate_limited",
                detail={"expert_id": expert.id},
            )
            exclude = exclude | {expert.id}
            continue

        if is_within_quiet_hours(clock.now(), config.defaults.quiet_hours, config.org.timezone):
            append_audit_event(
                session,
                ts=clock.now(),
                request_id=request.id,
                actor="system",
                event="ask.queued_quiet_hours",
                detail={"expert_id": expert.id},
            )
            return AskOutcome(status="queued_quiet_hours")

        return _deliver(session, scheduler, config, transport, llm_provider, clock, request, expert)


def _deliver(
    session: Session,
    scheduler: BackgroundScheduler,
    config: RegistryConfig,
    transport: Transport,
    llm_provider: LLMProvider | None,
    clock: Clock,
    request: ContextRequest,
    expert: ExpertRow,
) -> AskOutcome:
    question_text = draft_question_text(llm_provider, request=request, expert_name=expert.name)
    message = compose_message(request=request, expert_name=expert.name, question_text=question_text)
    delivery = transport.send_ask(expert_id=expert.id, expert_name=expert.name, message=message)

    now = clock.now()
    set_asked(
        session, request.id, expert_id=expert.id, asked_at=now, thread_ref=delivery.thread_ref
    )
    adjust_open_asks(session, expert.id, 1)
    record_ask_today(session, expert.id, today_str(clock))
    append_audit_event(
        session,
        ts=now,
        request_id=request.id,
        actor="system",
        event="ask.sent",
        detail={"expert_id": expert.id, "thread_ref": delivery.thread_ref},
    )

    escalation_after = request.routing_policy.escalation_after or config.defaults.escalation_after
    schedule_escalation(scheduler, session, request_id=request.id, fire_at=now + escalation_after)

    return AskOutcome(status="asked", expert_id=expert.id, thread_ref=delivery.thread_ref)
