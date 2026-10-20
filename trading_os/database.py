"""SQLite engine and session management."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

from fastapi import Request
from sqlalchemy import JSON, Column, Engine, create_engine, event, inspect, text
from sqlalchemy.orm import Session, sessionmaker

from .models import Base

# Bump when the models change; ``migrate`` brings older files up to date.
SCHEMA_VERSION = 3


def create_db_engine(db_path: Path) -> Engine:
    """Create an engine for ``db_path`` with foreign keys enforced."""
    engine = create_engine(
        f"sqlite:///{db_path}",
        connect_args={"check_same_thread": False},
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
        return "'[]'" if isinstance(column.type, JSON) else None
    value = default.arg
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, (int, float)):
        return repr(value)
    if isinstance(value, str):
        return "'" + value.replace("'", "''") + "'"
    return None


def migrate(engine: Engine) -> list[str]:
    """Add columns that the models define but an older database file lacks.

    SQLite can only *add* columns, which is all this app's schema evolution
    needs. Existing rows receive the column's default (or NULL). Returns the
    ``table.column`` names that were added.
    """
    inspector = inspect(engine)
    added: list[str] = []
    with engine.begin() as connection:
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
