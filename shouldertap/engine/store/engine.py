"""Engine/session factory. Both the running app and alembic/env.py resolve the DB URL through
`resolve_db_path`/`database_url_for` so they can never disagree about which file they're
pointing at (build-plan §"Alembic from day one").
"""

from __future__ import annotations

from pathlib import Path
from sqlite3 import Connection as SQLite3Connection

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import Session, sessionmaker


def resolve_db_path(config_path: Path) -> Path:
    """Spec §10.1: "Single file shouldertap.db next to the config"."""
    return config_path.parent / "shouldertap.db"


def database_url_for(db_path: Path) -> str:
    return f"sqlite:///{db_path}"


def configure_sqlite_engine(engine: Engine) -> None:
    """WAL mode plus a busy timeout so the app's own engine and APScheduler's independent
    SQLAlchemyJobStore engine -- two separate connections to the same file -- don't trip
    "database is locked" under concurrent access; SQLite's default rollback-journal mode allows
    only one writer at a time with no retry.
    """

    @event.listens_for(engine, "connect")
    def _set_sqlite_pragmas(
        dbapi_connection: SQLite3Connection, _connection_record: object
    ) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA busy_timeout=30000")
        cursor.close()


def make_engine(db_path: Path) -> Engine:
    engine = create_engine(
        database_url_for(db_path), connect_args={"check_same_thread": False, "timeout": 30}
    )
    configure_sqlite_engine(engine)
    return engine


def make_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, expire_on_commit=False)
