"""System status (v1.7): connectivity for the top-bar indicator and the Telegram/accounts detail."""

from fastapi import APIRouter, Request

from ..services import connectivity
from .deps import DbDep, SettingsDep

router = APIRouter(prefix="/api/status", tags=["status"])


def _monitor(request: Request) -> connectivity.InternetMonitor:
    return request.app.state.internet_monitor


@router.get("/connectivity")
def get_connectivity(request: Request, db: DbDep, settings: SettingsDep, refresh: bool = False):
    """Internet reachability (cached), broker bridge freshness and the Telegram listener state."""
    return connectivity.snapshot(db, settings, _monitor(request), force=refresh)
