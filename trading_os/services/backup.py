"""Export (CSV / JSON / ZIP) and restore of the whole journal.

* **CSV** - one row per trade, for spreadsheets and external analysis.
* **JSON** - every table, losslessly; can be restored.
* **ZIP** - the JSON plus all uploaded images (trade screenshots, Vision AI charts); can be restored.

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
    AlertRule,
    BacktestTrade,
    ChecklistDay,
    ChecklistItem,
    Confluence,
    DailyReview,
    Market,
    MistakeType,
    NewsEvent,
    NotificationLog,
    Partner,
    PropChallenge,
    Screenshot,
    Strategy,
    StrategyAlert,
    Trade,
    TradeEvent,
    TradeTarget,
    TradingSession,
    VisionAnalysis,
    VisionMessage,
)
from . import uploads
from .i18n import tr

SCHEMA_VERSION = 4
# Older backups restore fine: missing tables/columns fall back to defaults.
SUPPORTED_SCHEMA_VERSIONS = (1, 2, 3, 4)
# Secrets that stay on this machine: never exported, kept as-is on restore.
# (Notification channels and pushed candles are not part of backups at all.)
PRIVATE_COLUMNS = {
    "account_settings": (
        "ai_api_key",
        "vapid_public_key",
        "vapid_private_key",
        "market_api_keys",
        "allowed_hosts",
        "remote_password_hash",
        "remote_session_secret",
    ),
}
# Columns renamed since older backups were written: {table: {old key: new key}}.
LEGACY_KEYS = {"partners": {"referral_url": "link_url"}}
BACKUP_JSON_NAME = "backup.json"
MAX_UNPACKED_JSON = 512 * 1024 * 1024
MAX_UNPACKED_TOTAL = 2 * 1024 * 1024 * 1024

# Insert order respects foreign keys; deletion runs in reverse.
MODELS = (
    AccountSettings,
    Market,
    TradingSession,
    Strategy,
    AlertRule,
    Confluence,
    ChecklistItem,
    ChecklistDay,
    MistakeType,
    NewsEvent,
    DailyReview,
    Partner,
    PropChallenge,
    BacktestTrade,
    Trade,
    TradeTarget,
    TradeEvent,
    Screenshot,
    VisionAnalysis,
    VisionMessage,
    NotificationLog,
    StrategyAlert,
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
    "mistakes",
    "notes",
    "source",
    "targets",
    "screenshots",
)


class BackupError(ValueError):
    pass


def _jsonable(value):
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return value


def _row(obj) -> dict:
    private = PRIVATE_COLUMNS.get(obj.__tablename__, ())
    return {
        column.key: _jsonable(getattr(obj, column.key))
        for column in obj.__mapper__.columns
        if column.key not in private
    }


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
            elif column == "targets":
                value = "; ".join(f"{t.price:g}@{t.close_pct:g}%" for t in trade.targets)
            elif column in ("confluences", "tags", "mistakes"):
                value = _csv_text("; ".join(getattr(trade, column) or []))
            elif column in ("strategy", "session", "emotion", "notes", "timeframe", "symbol", "source"):
                value = _csv_text(getattr(trade, column) or "")
            else:
                value = _jsonable(getattr(trade, column))
                if isinstance(value, str):
                    value = _csv_text(value)
            row.append("" if value is None else value)
        writer.writerow(row)
    return buffer.getvalue()


def export_zip_bytes(db: Session, uploads_dir: Path) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(BACKUP_JSON_NAME, export_json_bytes(db))
        filenames = list(db.scalars(select(Screenshot.filename).where(Screenshot.kind == "file")))
        filenames += list(db.scalars(select(VisionAnalysis.filename)))  # Vision AI chart images
        for filename in dict.fromkeys(filenames):
            path = uploads_dir / (filename or "")
            if filename and uploads.is_safe_name(filename) and path.is_file():
                archive.write(path, f"uploads/{filename}")
    return buffer.getvalue()


def _coerce(model, row: dict) -> dict:
    values = {}
    private = PRIVATE_COLUMNS.get(model.__tablename__, ())
    for old, new in LEGACY_KEYS.get(model.__tablename__, {}).items():
        if old in row and new not in row:
            row = {**row, new: row[old]}
    for column in model.__mapper__.columns:
        if column.key not in row or column.key in private:
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
        raise BackupError(tr("Not a Trading OS backup file"))
    if payload.get("schema_version") not in SUPPORTED_SCHEMA_VERSIONS:
        raise BackupError(
            tr("Unsupported backup schema version {version}", version=repr(payload.get("schema_version")))
        )
    tables = payload.get("tables")
    if not isinstance(tables, dict):
        raise BackupError(tr("Backup file has no tables"))

    counts: dict[str, int] = {}
    current = db.get(AccountSettings, 1)
    kept_secrets = {key: getattr(current, key) for key in PRIVATE_COLUMNS["account_settings"]} if current else {}
    try:
        for model in reversed(MODELS):
            db.execute(delete(model))
        for model in MODELS:
            rows = tables.get(model.__tablename__, [])
            if not isinstance(rows, list):
                raise BackupError(tr("Table '{table}' is malformed", table=model.__tablename__))
            for row in rows:
                db.add(model(**_coerce(model, row)))
            db.flush()
            counts[model.__tablename__] = len(rows)
        settings = db.get(AccountSettings, 1)
        if settings is None:
            settings = AccountSettings(id=1)
            db.add(settings)
        for key, value in kept_secrets.items():
            setattr(settings, key, value)
        _validate_settings(settings)
        db.commit()
    except BackupError:
        db.rollback()
        raise
    except Exception as exc:  # malformed values, constraint violations, ...
        db.rollback()
        raise BackupError(tr("Backup could not be restored: {error}", error=exc)) from exc
    return counts


def _validate_settings(settings: AccountSettings) -> None:
    """A restored settings row must pass the same checks as the settings form (else the app can't load)."""
    from ..schemas import SettingsIn  # local import: schemas imports constants only, but keep backup light

    values = {name: getattr(settings, name) for name in SettingsIn.model_fields if hasattr(settings, name)}
    try:
        SettingsIn.model_validate(values)
    except ValueError as exc:
        first = exc.errors()[0] if hasattr(exc, "errors") else {}
        where = ".".join(str(part) for part in first.get("loc", ())) or "settings"
        raise BackupError(
            tr("The backup's settings are invalid ({where}: {message})", where=where, message=first.get("msg", exc))
        ) from exc


def write_safety_snapshot(db: Session, backups_dir: Path) -> Path:
    """Save the current data before a restore. Never overwrites an earlier snapshot."""
    stamp = f"{datetime.now():%Y%m%d-%H%M%S}"
    data = export_json_bytes(db)
    for attempt in range(1000):
        path = backups_dir / (f"pre-restore-{stamp}.json" if not attempt else f"pre-restore-{stamp}-{attempt}.json")
        try:
            with path.open("xb") as handle:
                handle.write(data)
            return path
        except FileExistsError:
            continue
    raise BackupError(tr("Could not write a safety snapshot"))


def restore_upload(
    db: Session, raw: bytes, filename: str, uploads_dir: Path, max_file_bytes: int = 25 * 1024 * 1024
) -> dict[str, int]:
    """Restore from an uploaded ``.json`` or ``.zip`` backup.

    ZIP members are size-checked before anything is extracted (no zip bombs): the
    backup JSON may use at most ``MAX_UNPACKED_JSON`` bytes, each image
    ``max_file_bytes`` and all images together ``MAX_UNPACKED_TOTAL``.
    """
    if filename.lower().endswith(".zip") or raw[:4] == b"PK\x03\x04":
        try:
            archive = zipfile.ZipFile(io.BytesIO(raw))
        except zipfile.BadZipFile as exc:
            raise BackupError(tr("Invalid ZIP archive")) from exc
        with archive:
            members = {info.filename: info for info in archive.infolist()}
            if BACKUP_JSON_NAME not in members:
                raise BackupError(tr("ZIP archive does not contain {name}", name=BACKUP_JSON_NAME))
            if members[BACKUP_JSON_NAME].file_size > MAX_UNPACKED_JSON:
                raise BackupError(tr("The backup data inside the ZIP is too large"))
            # Only flat, generated file names are extracted (no path traversal).
            images = [
                info
                for name, info in members.items()
                if name.partition("/")[0] == "uploads" and uploads.is_safe_name(name.partition("/")[2])
            ]
            if any(info.file_size > max_file_bytes for info in images):
                raise BackupError(tr("An image in the ZIP is larger than the upload limit"))
            if sum(info.file_size for info in images) > MAX_UNPACKED_TOTAL:
                raise BackupError(tr("The images in the ZIP are too large in total"))
            payload = _parse_json(archive.read(BACKUP_JSON_NAME))
            counts = restore_data(db, payload)
            for info in images:
                (uploads_dir / info.filename.partition("/")[2]).write_bytes(archive.read(info))
            counts["files"] = len(images)
            return counts
    return restore_data(db, _parse_json(raw))


def _parse_json(raw: bytes) -> dict:
    try:
        return json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise BackupError(tr("Backup file is not valid JSON")) from exc
