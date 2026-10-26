"""Liveness/health probe (v1.8): a tiny unauthenticated endpoint for container and uptime monitors.

``GET /api/health`` answers whether the process is up and the database is reachable. It is exempt from the
remote-access password (see ``services.network``) so a Docker ``HEALTHCHECK`` or an external monitor can poll it,
and it returns nothing sensitive — only the app name, version, UTC time and a one-word database state.
"""

from datetime import datetime, timezone

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from sqlalchemy import text

from .. import APP_NAME, __version__
from .deps import DbDep

router = APIRouter(prefix="/api", tags=["health"])


@router.get("/health", include_in_schema=False)
def health(db: DbDep):
    """200 when the database answers, 503 otherwise (so an orchestrator can restart a wedged container)."""
    try:
        db.execute(text("SELECT 1"))
        database = "ok"
    except Exception:  # noqa: BLE001 - any failure here means "not healthy", details stay in the logs
        database = "error"
    body = {
        "status": "ok" if database == "ok" else "degraded",
        "app": APP_NAME,
        "version": __version__,
        "database": database,
        "time": datetime.now(timezone.utc).isoformat(),
    }
    return JSONResponse(status_code=200 if database == "ok" else 503, content=body)
