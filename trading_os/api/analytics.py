"""Statistical dashboard endpoint."""

from datetime import datetime, time

from fastapi import APIRouter
from sqlalchemy import func, select

from ..models import Trade
from ..services import analytics
from ..services import trades as trade_service
from ..services.account import realized_pnl
from .deps import DbDep, SettingsDep
from .trades import FiltersDep

router = APIRouter(prefix="/api", tags=["analytics"])


@router.get("/analytics")
def read_analytics(settings: SettingsDep, db: DbDep, filters: FiltersDep):
    """Metrics, equity curve, daily P&L, R distribution and breakdowns.

    Filtering is on the *close* date. With a start date, the equity curve
    starts from the real account equity at that date, so drawdown % stays
    meaningful.
    """
    trades = trade_service.closed_trades(db, filters)
    base_equity = settings.starting_balance
    if filters.date_from:
        base_equity += realized_pnl(db, before=datetime.combine(filters.date_from, time.min))

    report = analytics.full_report(trades, base_equity)
    report["currency"] = settings.currency
    report["open_trades"] = db.scalar(select(func.count(Trade.id)).where(Trade.status == "open")) or 0
    return report
