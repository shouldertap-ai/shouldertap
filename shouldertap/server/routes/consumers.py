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


# response_model=None is load-bearing, not decoration: this module uses
# `from __future__ import annotations`, so `-> None` reaches FastAPI as the *string* "None".
# FastAPI < ~0.117 resolves that into a truthy response model and then trips its own
# "Status code 204 must not have a response body" assertion at import time -- taking down the
# whole CLI, since `shtap` imports the server module. Stating it explicitly keeps this working
# across the full `fastapi>=0.115` range we declare.
@router.delete("/consumers/{consumer_id}", status_code=204, response_model=None)
def unregister_consumer(
    consumer_id: str, session_factory: sessionmaker[Session] = Depends(get_session_factory)
) -> None:
    with session_factory() as session:
        delete_consumer(session, consumer_id)
        session.commit()
