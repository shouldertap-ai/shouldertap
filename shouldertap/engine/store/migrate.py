"""Runs Alembic migrations via its Python API (not a subprocess) so packaging/fresh-machine
setup doesn't depend on `alembic` being separately resolvable on $PATH -- called by `shtap serve`
on startup and by tests.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from alembic.config import Config

from alembic import command

_REPO_ROOT = Path(__file__).resolve().parents[3]


def run_migrations(db_path: Path) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    cfg = Config(str(_REPO_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(_REPO_ROOT / "alembic"))
    cfg.cmd_opts = argparse.Namespace(x=[f"db_path={db_path}"])
    command.upgrade(cfg, "head")
