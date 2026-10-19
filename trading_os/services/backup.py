"""Export (CSV / JSON / ZIP) and restore of the whole journal.

* **CSV** - one row per trade, for spreadsheets and external analysis.
* **JSON** - every table, losslessly; can be restored.
* **ZIP** - the JSON plus all uploaded screenshot files; can be restored.

A restore *replaces* all data. Before touching anything, a safety snapshot of
the current data is written to ``<data_dir>/backups/``.
"""

from __future__ import annotations

import csv
import io
import json
import zipfile
from datetime import date, datetime
from pathlib import Path

from sqlalchemy import Date, DateTime, delete, select
from sqlalchemy.orm import Session

from .. import __version__
from ..models import (
    AccountSettings,
    ChecklistDay,
    ChecklistItem,
    Confluence,
    Market,
    Screenshot,
    Strategy,
    Trade,
    TradingSession,
)
from . import uploads

SCHEMA_VERSION = 1
BACKUP_JSON_NAME = "backup.json"

# Insert order respects foreign keys; deletion runs in reverse.
MODELS = (
    AccountSettings,
    Market,
    TradingSession,
    Strategy,
    Confluence,
    ChecklistItem,
    ChecklistDay,
    Trade,
    Screenshot,
)

CSV_COLUMNS = (
    "id",
    "status",
    "entry_time",
    "exit_time",
    "symbol",
    "direction",
    "entry_price",
    "stop_loss",
    "take_profit",
    "exit_price",
    "position_size",
    "risk_amount",
    "fees",
    "pnl",
    "planned_rr",
    "executed_rr",
    "r_multiple",
    "outcome",
    "strategy",
    "timeframe",
    "session",
    "confluences",
    "emotion",
    "rule_compliant",
    "violations",
    "tags",
    "notes",
    "screenshots",
)


class BackupError(ValueError):
    pass


def _jsonable(value):
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return value


def _row(obj) -> dict:
    return {column.key: _jsonable(getattr(obj, column.key)) for column in obj.__mapper__.columns}


def export_data(db: Session) -> dict:
    return {
        "app": "trading_os",
        "app_version": __version__,
        "schema_version": SCHEMA_VERSION,
        "exported_at": datetime.now().isoformat(timespec="seconds"),
        "tables": {model.__tablename__: [_row(obj) for obj in db.scalars(select(model))] for model in MODELS},
    }


def export_json_bytes(db: Session) -> bytes:
    return json.dumps(export_data(db), indent=2, ensure_ascii=False).encode("utf-8")


def _csv_text(value: str) -> str:
    """Neutralise spreadsheet formula injection in free-text cells."""
    return "'" + value if value[:1] in ("=", "+", "-", "@", "\t", "\r") else value


def export_trades_csv(trades: list[Trade]) -> str:
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(CSV_COLUMNS)
    for trade in trades:
        row = []
        for column in CSV_COLUMNS:
            if column == "violations":
                value = "; ".join(v.get("code", "") for v in trade.violations or [])
            elif column == "screenshots":
                value = "; ".join(shot.src for shot in trade.screenshots)
            elif column in ("confluences", "tags"):
                value = _csv_text("; ".join(getattr(trade, column) or []))
            elif column in ("strategy", "session", "emotion", "notes", "timeframe"):
                value = _csv_text(getattr(trade, column) or "")
            else:
                value = _jsonable(getattr(trade, column))
            row.append("" if value is None else value)
        writer.writerow(row)
    return buffer.getvalue()


def export_zip_bytes(db: Session, uploads_dir: Path) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(BACKUP_JSON_NAME, export_json_bytes(db))
        filenames = db.scalars(select(Screenshot.filename).where(Screenshot.kind == "file"))
        for filename in filenames:
            path = uploads_dir / (filename or "")
            if filename and uploads.is_safe_name(filename) and path.is_file():
                archive.write(path, f"uploads/{filename}")
    return buffer.getvalue()


def _coerce(model, row: dict) -> dict:
    values = {}
    for column in model.__mapper__.columns:
        if column.key not in row:
            continue
        value = row[column.key]
        if value is not None and isinstance(column.type, DateTime):
            value = datetime.fromisoformat(value)
        elif value is not None and isinstance(column.type, Date):
            value = date.fromisoformat(value)
        values[column.key] = value
    return values


def restore_data(db: Session, payload: dict) -> dict[str, int]:
    """Replace every table with the content of ``payload``. Atomic."""
    if not isinstance(payload, dict) or payload.get("app") != "trading_os":
        raise BackupError("Not a Trading OS backup file")
    if payload.get("schema_version") != SCHEMA_VERSION:
        raise BackupError(f"Unsupported backup schema version {payload.get('schema_version')!r}")
    tables = payload.get("tables")
    if not isinstance(tables, dict):
        raise BackupError("Backup file has no tables")

    counts: dict[str, int] = {}
    try:
        for model in reversed(MODELS):
            db.execute(delete(model))
        for model in MODELS:
            rows = tables.get(model.__tablename__, [])
            if not isinstance(rows, list):
                raise BackupError(f"Table '{model.__tablename__}' is malformed")
            for row in rows:
                db.add(model(**_coerce(model, row)))
            db.flush()
            counts[model.__tablename__] = len(rows)
        if db.get(AccountSettings, 1) is None:
            db.add(AccountSettings(id=1))
        db.commit()
    except BackupError:
        db.rollback()
        raise
    except Exception as exc:  # malformed values, constraint violations, ...
        db.rollback()
        raise BackupError(f"Backup could not be restored: {exc}") from exc
    return counts


def write_safety_snapshot(db: Session, backups_dir: Path) -> Path:
    path = backups_dir / f"pre-restore-{datetime.now():%Y%m%d-%H%M%S}.json"
    path.write_bytes(export_json_bytes(db))
    return path


def restore_upload(db: Session, raw: bytes, filename: str, uploads_dir: Path) -> dict[str, int]:
    """Restore from an uploaded ``.json`` or ``.zip`` backup."""
    if filename.lower().endswith(".zip") or raw[:4] == b"PK\x03\x04":
        try:
            archive = zipfile.ZipFile(io.BytesIO(raw))
        except zipfile.BadZipFile as exc:
            raise BackupError("Invalid ZIP archive") from exc
        with archive:
            if BACKUP_JSON_NAME not in archive.namelist():
                raise BackupError(f"ZIP archive does not contain {BACKUP_JSON_NAME}")
            payload = _parse_json(archive.read(BACKUP_JSON_NAME))
            counts = restore_data(db, payload)
            restored_files = 0
            for name in archive.namelist():
                # Only flat, generated file names are extracted (no path traversal).
                prefix, _, base = name.partition("/")
                if prefix == "uploads" and uploads.is_safe_name(base):
                    (uploads_dir / base).write_bytes(archive.read(name))
                    restored_files += 1
            counts["files"] = restored_files
            return counts
    return restore_data(db, _parse_json(raw))


def _parse_json(raw: bytes) -> dict:
    try:
        return json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise BackupError("Backup file is not valid JSON") from exc
