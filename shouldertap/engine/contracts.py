"""The stable public protocol (spec §4): ContextRequest, ContextProposal, and the consumer
registration/callback shapes. These are mirrored as JSON Schema under spec/schemas/.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from enum import StrEnum
from typing import Annotated, Any, Literal, Protocol

from pydantic import BaseModel, Field

from shouldertap.engine.clock import utcnow
from shouldertap.engine.ids import new_id

# --- Built-in kinds (spec §8.2). `Kind` itself is an open string -- consumers may register
# their own with a custom capture schema at registration time. ---
KIND_GLOSSARY_DEFINITION = "glossary.definition"
KIND_FREEFORM_ANSWER = "freeform.answer"

Kind = str


class ReasonCode(StrEnum):
    """Spec §4.4 -- the exact enumerated failure reasons. TIMEOUT/NO_EXPERT_FOUND/
    EXPERT_DECLINED/RATE_LIMITED are all reachable in v0.1 (see facade.py/capture.py).
    CANCELLED and ERROR are part of the contract but currently unreachable: v0.1 has no cancel
    endpoint/CLI verb (not spec-mandated), and unexpected failures degrade gracefully rather
    than surfacing as a generic error (e.g. LLM failures -> structured=null, not a failed
    request) -- kept in the enum for wire-compatibility with future versions that add either.
    """

    TIMEOUT = "timeout"
    NO_EXPERT_FOUND = "no_expert_found"
    EXPERT_DECLINED = "expert_declined"
    RATE_LIMITED = "rate_limited"
    CANCELLED = "cancelled"
    ERROR = "error"


class Reason(BaseModel):
    code: ReasonCode
    detail: str | None = None


class RequestStatus(StrEnum):
    """Spec §5 -- the status values a ContextRequest moves through."""

    QUEUED = "queued"
    ASKED = "asked"
    ESCALATED = "escalated"
    PROPOSED = "proposed"
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    FAILED = "failed"


class TargetExpert(BaseModel):
    expert_id: str
    role: str = "primary"


class RoutingPolicy(BaseModel):
    """Spec §4.1. Durations are ISO-8601 (e.g. "PT2H"); Pydantic parses these into timedelta.
    Any field left unset is filled from shouldertap.yaml's `defaults:` section by the router.
    """

    primary_experts: list[str] = Field(default_factory=list)
    escalation_targets: list[str] = Field(default_factory=list)
    escalation_after: timedelta | None = None
    give_up_after: timedelta | None = None
    priority: int = 50


class ContextRequest(BaseModel):
    """Spec §4.1."""

    id: str = Field(default_factory=lambda: new_id("req"))
    org_id: str = "default"
    kind: Kind
    topic: str
    question: str
    context: dict[str, Any] = Field(default_factory=dict)
    target_experts: list[TargetExpert] | None = None
    routing_policy: RoutingPolicy = Field(default_factory=RoutingPolicy)
    consumer: str
    dedup_key: str | None = None
    correlation: dict[str, Any] | None = None
    created_at: datetime = Field(default_factory=utcnow)

    @property
    def normalized_topic(self) -> str:
        """Routing key form: lowercase, collapsed whitespace -- spec §6 step 2."""
        return " ".join(self.topic.lower().split())


class Provenance(BaseModel):
    """Spec §4.2."""

    expert_id: str
    expert_name: str
    answered_via: str
    slack_thread_ts: str | None = None
    answered_at: datetime
    escalated: bool = False


class ContextProposal(BaseModel):
    """Spec §4.2."""

    id: str = Field(default_factory=lambda: new_id("prop"))
    request_id: str
    kind: Kind
    answer: str
    structured: dict[str, Any] | None = None
    provenance: Provenance
    confidence: float | None = None
    consumer: str
    created_at: datetime = Field(default_factory=utcnow)


# --- Consumer registration (spec §4.3) ---


class WebhookDelivery(BaseModel):
    type: Literal["webhook"] = "webhook"
    url: str


class InProcessDelivery(BaseModel):
    """Marker for an SDK-embedded consumer whose callables are registered in-process and
    are never serialized -- see shouldertap/sdk/client.py.
    """

    type: Literal["inprocess"] = "inprocess"


Delivery = Annotated[WebhookDelivery | InProcessDelivery, Field(discriminator="type")]


class ConsumerRegistration(BaseModel):
    """Spec §4.3: id, handles_kinds, delivery (webhook URL or SDK callback), dedup_window."""

    id: str
    # Declarative only in v0.1: delivery is driven entirely by each request's `subscribers`
    # list (the submitting consumer plus any dedup fan-out), not by broadcasting to every
    # consumer registered for a given kind -- nothing in the spec calls for that.
    handles_kinds: list[Kind]
    delivery: Delivery
    dedup_window: timedelta = timedelta(hours=24)
    auto_accept: bool = False
    kind_schemas: dict[str, dict[str, Any]] = Field(default_factory=dict)


class ConsumerCallbacks(Protocol):
    """Spec §4.3 -- the four callback names a consumer implements, whether invoked in-process
    (SDK) or delivered as webhook POSTs by the server layer.
    """

    def on_proposal(self, proposal: ContextProposal) -> None: ...

    def on_proposal_accepted(self, proposal: ContextProposal) -> None: ...

    def on_proposal_rejected(self, proposal: ContextProposal, reason: str) -> None: ...

    def on_request_failed(self, request_id: str, reason: Reason) -> None: ...
