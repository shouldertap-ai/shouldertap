"""Runs Alembic migrations via its Python API (not a subprocess) so packaging/fresh-machine
setup doesn't depend on `alembic` being separately resolvable on $PATH -- called by `shtap serve`
on startup and by tests.

The migration scripts live *inside* the installed package (`shouldertap/migrations/`), not at
the repo root, and the Alembic Config is built programmatically rather than read from a
repo-root `alembic.ini`. Both matter for `pip install shouldertap`: anything outside the
package directory simply isn't in the wheel, so a real (non-editable) install would otherwise
crash on first startup with a confusing "Can't find Python file .../site-packages/alembic/env.py".
"""

from __future__ import annotations

import argparse
from pathlib import Path

from alembic import command
from alembic.config import Config

MIGRATIONS_DIR = Path(__file__).resolve().parents[2] / "migrations"


def make_alembic_config(db_path: Path) -> Config:
    cfg = Config()
    cfg.set_main_option("script_location", str(MIGRATIONS_DIR))
    # env.py reads this via -x db_path=... so the app and Alembic can never disagree about
    # which file they're pointing at.
    cfg.cmd_opts = argparse.Namespace(x=[f"db_path={db_path}"])
    return cfg


def run_migrations(db_path: Path) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    command.upgrade(make_alembic_config(db_path), "head")
