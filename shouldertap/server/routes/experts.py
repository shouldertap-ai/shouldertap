"""Spec §5/§6: GET/PUT /experts -- the config file remains the source of truth; PUT rewrites it
and re-syncs the `experts` table (full replace semantics, not a merge).
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session, sessionmaker

from shouldertap.engine.registry import ExpertConfig, RegistryConfig, save_config
from shouldertap.engine.store.models import ExpertRow
from shouldertap.engine.store.repository import list_all_experts, replace_experts
from shouldertap.server.deps import get_config, get_config_path, get_session_factory

router = APIRouter()


class ExpertOut(BaseModel):
    id: str
    name: str
    topics: list[str]
    escalation_to: str | None
    muted: bool
    open_asks: int
    asks_today: int


def _to_out(row: ExpertRow) -> ExpertOut:
    return ExpertOut(
        id=row.id,
        name=row.name,
        topics=row.topics,
        escalation_to=row.escalation_to,
        muted=row.muted,
        open_asks=row.open_asks,
        asks_today=row.asks_today,
    )


@router.get("/experts")
def get_experts(
    session_factory: sessionmaker[Session] = Depends(get_session_factory),
) -> list[ExpertOut]:
    with session_factory() as session:
        return [_to_out(row) for row in list_all_experts(session)]


@router.put("/experts")
def put_experts(
    experts: list[ExpertConfig],
    session_factory: sessionmaker[Session] = Depends(get_session_factory),
    config: RegistryConfig = Depends(get_config),
    config_path: Path = Depends(get_config_path),
) -> list[ExpertOut]:
    config.experts = experts
    save_config(config, config_path)
    with session_factory() as session:
        replace_experts(session, [(e.id, e.name, e.topics, e.escalation_to) for e in experts])
        session.commit()
        return [_to_out(row) for row in list_all_experts(session)]
