"""Data export (CSV / JSON / ZIP) and restore."""

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, File, UploadFile
from fastapi.responses import Response

from ..services import backup
from ..services import trades as trade_service
from ..services.i18n import tr
from .deps import ConfigDep, DbDep

router = APIRouter(prefix="/api", tags=["backup"])

MAX_RESTORE_BYTES = 512 * 1024 * 1024


def _download(content: bytes | str, filename: str, media_type: str) -> Response:
    return Response(
        content=content,
        media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


def _stamp() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M%S")


@router.get("/export/trades.csv")
def export_csv(db: DbDep):
    trades, _ = trade_service.list_trades(db, trade_service.TradeFilters(), sort="entry_time", descending=False)
    # UTF-8 BOM so Excel detects the encoding.
    return _download("﻿" + backup.export_trades_csv(trades), f"trades-{_stamp()}.csv", "text/csv; charset=utf-8")


@router.get("/export/backup.json")
def export_json(db: DbDep):
    return _download(backup.export_json_bytes(db), f"trading-os-backup-{_stamp()}.json", "application/json")


@router.get("/export/backup.zip")
def export_zip(db: DbDep, config: ConfigDep):
    return _download(
        backup.export_zip_bytes(db, config.uploads_dir), f"trading-os-backup-{_stamp()}.zip", "application/zip"
    )


@router.post("/import")
async def import_backup(db: DbDep, config: ConfigDep, file: Annotated[UploadFile, File()]):
    raw = await file.read(MAX_RESTORE_BYTES + 1)
    if len(raw) > MAX_RESTORE_BYTES:
        raise backup.BackupError(tr("Backup file is too large"))
    snapshot = backup.write_safety_snapshot(db, config.backups_dir)
    counts = backup.restore_upload(db, raw, file.filename or "", config.uploads_dir, config.max_upload_bytes)
    return {"restored": counts, "safety_snapshot": snapshot.name}
