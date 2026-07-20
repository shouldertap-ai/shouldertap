"""Spec §5: POST /consumers, DELETE /consumers/{id}."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session, sessionmaker

from shouldertap.engine.contracts import ConsumerRegistration
from shouldertap.engine.store.repository import delete_consumer, upsert_consumer
from shouldertap.server.deps import get_session_factory

router = APIRouter()


@router.post("/consumers", status_code=201)
def register_consumer(
    registration: ConsumerRegistration,
    session_factory: sessionmaker[Session] = Depends(get_session_factory),
) -> dict[str, Any]:
    with session_factory() as session:
        upsert_consumer(session, registration)
        session.commit()
    return {"id": registration.id}


@router.delete("/consumers/{consumer_id}", status_code=204)
def unregister_consumer(
    consumer_id: str, session_factory: sessionmaker[Session] = Depends(get_session_factory)
) -> None:
    with session_factory() as session:
        delete_consumer(session, consumer_id)
        session.commit()
