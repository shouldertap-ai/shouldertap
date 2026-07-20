"""Spec §5: POST /requests, GET /requests/{id}."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session, sessionmaker

from shouldertap.engine.contracts import ContextRequest
from shouldertap.engine.facade import Facade
from shouldertap.engine.store.mappers import row_to_proposal, row_to_request
from shouldertap.engine.store.repository import get_proposal, get_request
from shouldertap.server.deps import get_facade, get_session_factory

router = APIRouter()


@router.post("/requests")
def submit_request(request: ContextRequest, facade: Facade = Depends(get_facade)) -> dict[str, Any]:
    outcome = facade.submit_request(request)
    return {"id": outcome.request_id, "status": outcome.status}


@router.get("/requests/{request_id}")
def get_request_detail(
    request_id: str,
    session_factory: sessionmaker[Session] = Depends(get_session_factory),
) -> dict[str, Any]:
    with session_factory() as session:
        row = get_request(session, request_id)
        if row is None:
            raise HTTPException(status_code=404, detail="request not found")

        proposal = None
        if row.resolved_proposal_id:
            proposal_row = get_proposal(session, row.resolved_proposal_id)
            if proposal_row is not None:
                proposal = row_to_proposal(proposal_row).model_dump(mode="json")

        # spec §5: "full request + status + proposal if any" -- row_to_request() reconstructs
        # every normative ContextRequest field (context, routing_policy, dedup_key,
        # correlation, target_experts, created_at, ...), not just the subset this route used
        # to hand-assemble. Runtime/store-only fields (status, subscribers, etc.) are layered
        # on top since they're not part of the wire ContextRequest contract.
        full_request = row_to_request(row).model_dump(mode="json")
        return {
            **full_request,
            "status": row.status,
            "subscribers": row.subscribers,
            "asked_expert_id": row.asked_expert_id,
            "escalated": row.escalated,
            "failure_reason": row.failure_reason,
            "proposal": proposal,
        }
