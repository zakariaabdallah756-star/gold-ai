"""Strategy incubator and backtesting (track BT)."""

from fastapi import APIRouter

router = APIRouter(prefix="/api", tags=["backtest"])
