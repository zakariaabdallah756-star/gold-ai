"""Strategy alert rules, scanner and alert feed (track S)."""

from fastapi import APIRouter

router = APIRouter(prefix="/api/alerts", tags=["alerts"])
