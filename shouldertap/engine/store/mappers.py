"""ORM <-> Pydantic translation. The ORM (models.py) is the storage-schema source of truth;
contracts.py is the wire/public shape -- kept deliberately separate rather than merged (no
SQLModel), per the mandated stack naming SQLAlchemy specifically.
"""

from __future__ import annotations

from shouldertap.engine.contracts import ContextProposal, ContextRequest, TargetExpert
from shouldertap.engine.store.models import ProposalRow, RequestRow


def request_to_row(request: ContextRequest, *, status: str = "queued") -> RequestRow:
    return RequestRow(
        id=request.id,
        org_id=request.org_id,
        kind=request.kind,
        topic=request.topic,
        question=request.question,
        context=request.context,
        target_experts=(
            [t.model_dump(mode="json") for t in request.target_experts]
            if request.target_experts is not None
            else None
        ),
        routing_policy=request.routing_policy.model_dump(mode="json"),
        consumer=request.consumer,
        dedup_key=request.dedup_key,
        correlation=request.correlation,
        subscribers=[request.consumer],
        status=status,
        created_at=request.created_at,
    )


def row_to_request(row: RequestRow) -> ContextRequest:
    return ContextRequest(
        id=row.id,
        org_id=row.org_id,
        kind=row.kind,
        topic=row.topic,
        question=row.question,
        context=row.context,
        target_experts=(
            [TargetExpert.model_validate(t) for t in row.target_experts]
            if row.target_experts is not None
            else None
        ),
        routing_policy=row.routing_policy,
        consumer=row.consumer,
        dedup_key=row.dedup_key,
        correlation=row.correlation,
        created_at=row.created_at,
    )


def proposal_to_row(proposal: ContextProposal, *, status: str = "pending") -> ProposalRow:
    return ProposalRow(
        id=proposal.id,
        request_id=proposal.request_id,
        kind=proposal.kind,
        answer=proposal.answer,
        structured=proposal.structured,
        provenance=proposal.provenance.model_dump(mode="json"),
        confidence=proposal.confidence,
        consumer=proposal.consumer,
        status=status,
        created_at=proposal.created_at,
    )


def row_to_proposal(row: ProposalRow) -> ContextProposal:
    return ContextProposal(
        id=row.id,
        request_id=row.request_id,
        kind=row.kind,
        answer=row.answer,
        structured=row.structured,
        provenance=row.provenance,
        confidence=row.confidence,
        consumer=row.consumer,
        created_at=row.created_at,
    )
