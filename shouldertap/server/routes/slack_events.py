"""Spec §5: POST /slack/events -- the Slack Events API endpoint (URL verification + DM message
events). `app.state.slack_events_handler` is set in app.py only when the app is running with
the Slack transport (see create_app); otherwise this reports itself unconfigured rather than
404ing, so the manifest can point at it from day one regardless of which transport is active.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from starlette.responses import Response

router = APIRouter()


@router.post("/slack/events")
async def slack_events(request: Request) -> Response:
    handler = getattr(request.app.state, "slack_events_handler", None)
    if handler is None:
        raise HTTPException(status_code=501, detail="Slack transport is not configured")
    response: Response = await handler(request)
    return response
