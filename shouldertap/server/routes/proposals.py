"""Spec §5: GET /proposals?status=pending, POST /proposals/{id}/accept|reject."""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session, sessionmaker

from shouldertap.engine.facade import Facade
from shouldertap.engine.store.mappers import row_to_proposal
from shouldertap.engine.store.repository import list_pending_proposals
from shouldertap.server.deps import get_facade, get_session_factory

router = APIRouter()


class AcceptBody(BaseModel):
    decided_by: str
    note: str | None = None


class RejectBody(BaseModel):
    decided_by: str
    reason: str


@router.get("/proposals")
def list_proposals(
    status: Literal["pending"] | None = None,
    session_factory: sessionmaker[Session] = Depends(get_session_factory),
) -> list[dict[str, Any]]:
    # spec's approval queue is the pending set; status is the only filter value in v0.1.
    if status not in (None, "pending"):
        return []
    with session_factory() as session:
        rows = list_pending_proposals(session)
        return [
            {**row_to_proposal(row).model_dump(mode="json"), "status": row.status} for row in rows
        ]


@router.post("/proposals/{proposal_id}/accept")
def accept_proposal(
    proposal_id: str, body: AcceptBody, facade: Facade = Depends(get_facade)
) -> dict[str, Any]:
    result = facade.accept_proposal(
        proposal_id=proposal_id, decided_by=body.decided_by, note=body.note
    )
    if result is None:
        raise HTTPException(status_code=404, detail="proposal not found or not pending")
    return result.model_dump(mode="json")


@router.post("/proposals/{proposal_id}/reject")
def reject_proposal(
    proposal_id: str, body: RejectBody, facade: Facade = Depends(get_facade)
) -> dict[str, Any]:
    result = facade.reject_proposal(
        proposal_id=proposal_id, decided_by=body.decided_by, reason=body.reason
    )
    if result is None:
        raise HTTPException(status_code=404, detail="proposal not found or not pending")
    return result.model_dump(mode="json")
