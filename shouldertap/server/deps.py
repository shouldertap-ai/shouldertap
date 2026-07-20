"""FastAPI dependency accessors -- everything the routes need lives on `app.state`, set up once
in app.py's create_app().
"""

from __future__ import annotations

from pathlib import Path
from typing import cast

from fastapi import Request
from sqlalchemy.orm import Session, sessionmaker

from shouldertap.engine.facade import Facade
from shouldertap.engine.registry import RegistryConfig


def get_facade(request: Request) -> Facade:
    return cast(Facade, request.app.state.facade)


def get_session_factory(request: Request) -> sessionmaker[Session]:
    return cast("sessionmaker[Session]", request.app.state.session_factory)


def get_config(request: Request) -> RegistryConfig:
    return cast(RegistryConfig, request.app.state.config)


def get_config_path(request: Request) -> Path:
    return cast(Path, request.app.state.config_path)
