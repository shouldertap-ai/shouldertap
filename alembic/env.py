from __future__ import annotations

import os
from logging.config import fileConfig
from pathlib import Path

from sqlalchemy import engine_from_config, pool

from alembic import context
from shouldertap.engine.store.engine import database_url_for
from shouldertap.engine.store.models import Base

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def _resolve_db_path() -> Path:
    """Same resolution order the app uses (see shouldertap.engine.store.migrate.run_migrations):
    an explicit `-x db_path=...` override first (what the running app/tests pass), then the
    SHOULDERTAP_DB_PATH env var, then ./shouldertap.db in the current directory.
    """
    x_args = context.get_x_argument(as_dictionary=True)
    if "db_path" in x_args:
        return Path(x_args["db_path"])
    env_path = os.environ.get("SHOULDERTAP_DB_PATH")
    if env_path:
        return Path(env_path)
    return Path.cwd() / "shouldertap.db"


def run_migrations_offline() -> None:
    url = database_url_for(_resolve_db_path())
    context.configure(url=url, target_metadata=target_metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    configuration = config.get_section(config.config_ini_section) or {}
    configuration["sqlalchemy.url"] = database_url_for(_resolve_db_path())
    connectable = engine_from_config(configuration, prefix="sqlalchemy.", poolclass=pool.NullPool)
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
