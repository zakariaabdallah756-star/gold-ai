"""SQLite engine and session management."""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

from fastapi import Request
from sqlalchemy import JSON, Column, Engine, create_engine, event, inspect, text
from sqlalchemy.orm import Session, sessionmaker

from .models import Base

# Bump when the models change; ``migrate`` brings older files up to date.
SCHEMA_VERSION = 7

# Columns renamed between versions: (table, old name, new name).
RENAMED_COLUMNS = (("partners", "referral_url", "link_url"),)


def create_db_engine(db_path: Path) -> Engine:
    """Create an engine for ``db_path`` with foreign keys enforced."""
    engine = create_engine(
        f"sqlite:///{db_path}",
        # The background scheduler writes from its own thread: wait for a busy database instead of failing.
        connect_args={"check_same_thread": False, "timeout": 15},
    )

    @event.listens_for(engine, "connect")
    def _enable_foreign_keys(dbapi_connection, _record) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    return engine


def init_db(engine: Engine) -> sessionmaker[Session]:
    """Create missing tables, add missing columns, return a session factory."""
    Base.metadata.create_all(engine)
    migrate(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)


def _default_sql(column: Column) -> str | None:
    """SQL literal for a column's Python-side default, if it has a constant one."""
    default = column.default
    if default is None:
        return None
    if default.is_callable:
        if not isinstance(column.type, JSON):
            return None
        value = default.arg(None)  # SQLAlchemy wraps zero-argument callables to accept a context
        return "'" + json.dumps(value).replace("'", "''") + "'"
    value = default.arg
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, (int, float)):
        return repr(value)
    if isinstance(value, str):
        return "'" + value.replace("'", "''") + "'"
    return None


def migrate(engine: Engine) -> list[str]:
    """Rename columns listed in ``RENAMED_COLUMNS`` and add columns an older database file lacks.

    Existing rows receive the column's default (or NULL). Returns the
    ``table.column`` names that were added.
    """
    inspector = inspect(engine)
    added: list[str] = []
    with engine.begin() as connection:
        for table_name, old_name, new_name in RENAMED_COLUMNS:
            if not inspector.has_table(table_name):
                continue
            names = {column["name"] for column in inspector.get_columns(table_name)}
            if old_name in names and new_name not in names:
                connection.execute(text(f'ALTER TABLE "{table_name}" RENAME COLUMN "{old_name}" TO "{new_name}"'))
        inspector = inspect(connection)
        for table in Base.metadata.sorted_tables:
            if not inspector.has_table(table.name):
                continue
            existing = {column["name"] for column in inspector.get_columns(table.name)}
            for column in table.columns:
                if column.name in existing:
                    continue
                ddl = f'ALTER TABLE "{table.name}" ADD COLUMN "{column.name}" {column.type.compile(engine.dialect)}'
                default = _default_sql(column)
                if default is not None:
                    ddl += f" NOT NULL DEFAULT {default}" if not column.nullable else f" DEFAULT {default}"
                connection.execute(text(ddl))
                added.append(f"{table.name}.{column.name}")
        connection.execute(text(f"PRAGMA user_version = {SCHEMA_VERSION}"))
    return added


def get_db(request: Request) -> Iterator[Session]:
    """FastAPI dependency yielding a session from the app's session factory."""
    session: Session = request.app.state.session_factory()
    try:
        yield session
    finally:
        session.close()
