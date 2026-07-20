"""Spec §5/§10.2: GET /audit?request_id= -- the full trail for one tap, end to end."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session, sessionmaker

from shouldertap.engine.store.repository import list_audit_events
from shouldertap.server.deps import get_session_factory

router = APIRouter()


@router.get("/audit")
def get_audit(
    request_id: str, session_factory: sessionmaker[Session] = Depends(get_session_factory)
) -> list[dict[str, Any]]:
    with session_factory() as session:
        events = list_audit_events(session, request_id)
        return [
            {
                "ts": e.ts.isoformat(),
                "request_id": e.request_id,
                "actor": e.actor,
                "event": e.event,
                "detail": e.detail,
            }
            for e in events
        ]
