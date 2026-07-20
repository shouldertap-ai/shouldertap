"""FastAPI app factory: mounts every §5 route under /api/v1, the approval UI at /, runs
migrations and syncs config experts on startup, and owns the scheduler lifecycle. This process
is the one that touches the SQLite file directly (build plan) -- CLI and MCP are HTTP clients
of whatever's running here.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, Request
from starlette.responses import Response

from shouldertap.engine.clock import SystemClock
from shouldertap.engine.delivery import ConsumerDeliverer
from shouldertap.engine.facade import Facade
from shouldertap.engine.llm import build_llm_provider
from shouldertap.engine.registry import load_config
from shouldertap.engine.scheduler.core import ensure_quiet_hours_sweep, make_scheduler
from shouldertap.engine.store.engine import make_engine, make_session_factory, resolve_db_path
from shouldertap.engine.store.migrate import run_migrations
from shouldertap.engine.store.repository import upsert_expert
from shouldertap.engine.transports.base import Transport
from shouldertap.engine.transports.console import ConsoleTransport
from shouldertap.engine.transports.slack import SlackTransport
from shouldertap.server.approval_ui.routes import router as approval_ui_router
from shouldertap.server.auth import require_bearer_token
from shouldertap.server.routes.audit import router as audit_router
from shouldertap.server.routes.consumers import router as consumers_router
from shouldertap.server.routes.experts import router as experts_router
from shouldertap.server.routes.proposals import router as proposals_router
from shouldertap.server.routes.requests import router as requests_router
from shouldertap.server.routes.slack_events import router as slack_events_router

logger = logging.getLogger(__name__)


def create_app(config_path: Path, *, transport: Transport | None = None) -> FastAPI:
    config = load_config(config_path)
    db_path = resolve_db_path(config_path)
    run_migrations(db_path)
    engine = make_engine(db_path)
    session_factory = make_session_factory(engine)

    with session_factory() as session:
        for expert in config.experts:
            upsert_expert(
                session,
                expert_id=expert.id,
                name=expert.name,
                topics=expert.topics,
                escalation_to=expert.escalation_to,
            )
        session.commit()

    scheduler = make_scheduler(db_path)
    llm_provider = build_llm_provider(config)
    deliverer = ConsumerDeliverer()
    resolved_transport: Transport = transport if transport is not None else ConsoleTransport()

    facade = Facade(
        session_factory=session_factory,
        scheduler=scheduler,
        config=config,
        transport=resolved_transport,
        llm_provider=llm_provider,
        deliverer=deliverer,
        clock=SystemClock(),
    )

    if config.api_token() is None:
        logger.warning(
            "SHOULDERTAP_API_TOKEN is not set (%s) -- the HTTP API is running with no auth.",
            config.server.api_token_env,
        )

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        ensure_quiet_hours_sweep(scheduler)
        scheduler.start()
        try:
            yield
        finally:
            scheduler.shutdown(wait=False)

    app = FastAPI(title="ShoulderTap", version="0.1.0", lifespan=lifespan)
    app.state.config = config
    app.state.config_path = config_path
    app.state.session_factory = session_factory
    app.state.facade = facade
    app.state.scheduler = scheduler
    app.state.deliverer = deliverer

    if isinstance(resolved_transport, SlackTransport):
        from slack_bolt.adapter.fastapi import SlackRequestHandler

        slack_request_handler = SlackRequestHandler(resolved_transport.app)

        async def _handle_slack_events(request: Request) -> Response:
            return await slack_request_handler.handle(request)

        app.state.slack_events_handler = _handle_slack_events

    authed = [Depends(require_bearer_token)]
    app.include_router(requests_router, prefix="/api/v1", dependencies=authed)
    app.include_router(consumers_router, prefix="/api/v1", dependencies=authed)
    app.include_router(proposals_router, prefix="/api/v1", dependencies=authed)
    app.include_router(experts_router, prefix="/api/v1", dependencies=authed)
    app.include_router(audit_router, prefix="/api/v1", dependencies=authed)
    app.include_router(slack_events_router, prefix="/api/v1")  # Slack signs its own requests
    app.include_router(approval_ui_router)

    return app
