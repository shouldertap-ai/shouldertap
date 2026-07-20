"""Spec §5: "Auth: single static bearer token from config (`api_token`). No user accounts in
v0.1." If no token is configured (env var unset), the API runs open -- logged as a startup
warning in app.py rather than silently, since that's an easy footgun for a real deployment.
"""

from __future__ import annotations

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

_bearer_scheme = HTTPBearer(auto_error=False)


def require_bearer_token(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer_scheme),
) -> None:
    expected = request.app.state.config.api_token()
    if expected is None:
        return
    if credentials is None or credentials.credentials != expected:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid or missing bearer token"
        )
