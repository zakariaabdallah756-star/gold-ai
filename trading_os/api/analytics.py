"""Statistical dashboard, P&L calendar and day-detail endpoints."""

from datetime import date, datetime, time
from typing import Annotated

from fastapi import APIRouter, Query
from pydantic import BaseModel
from sqlalchemy import func, select

from ..models import DailyReview, Trade
from ..schemas import TradeOut
from ..services import analytics, reviews, time_analytics
from ..services import trades as trade_service
from ..services.account import local_today, realized_pnl
from .deps import DbDep, SettingsDep
from .trades import FiltersDep

router = APIRouter(prefix="/api", tags=["analytics"])

MonthParam = Annotated[str | None, Query(pattern=r"^[12]\d{3}-(0[1-9]|1[0-2])$", description="YYYY-MM")]


class DayDetailOut(BaseModel):
    date: str
    trades: list[TradeOut]
    pnl: float
    review: dict | None
    checklist_complete: bool


@router.get("/analytics")
def read_analytics(settings: SettingsDep, db: DbDep, filters: FiltersDep):
    """Metrics, equity curve, daily P&L, R distribution, breakdowns and the time-of-day heatmap.

    Filtering is on the *close* date. With a start date, the equity curve
    starts from the real account equity at that date, so drawdown % stays
    meaningful.
    """
    trades = trade_service.closed_trades(db, filters)
    base_equity = settings.starting_balance
    if filters.date_from:
        base_equity += realized_pnl(db, before=datetime.combine(filters.date_from, time.min))

    report = analytics.full_report(trades, base_equity)
    report["time_heatmap"] = time_analytics.heatmap(trades)
    report["currency"] = settings.currency
    report["open_trades"] = db.scalar(select(func.count(Trade.id)).where(Trade.status == "open")) or 0
    return report


@router.get("/analytics/calendar")
def read_calendar(settings: SettingsDep, db: DbDep, month: MonthParam = None):
    """P&L calendar of a month (default: the current account-local month), by close date."""
    first = time_analytics.parse_month(month) if month else local_today(settings).replace(day=1)
    _, _, start, end = time_analytics.month_grid(first)
    report = time_analytics.calendar_month(
        reviews.closed_between(db, start, end),
        first,
        reviewed=reviews.reviewed_days(db, start, end),
        checklist_complete=reviews.checklist_completed_days(db, start, end),
    )
    report["currency"] = settings.currency
    return report


@router.get("/analytics/day/{day}", response_model=DayDetailOut)
def read_day(day: date, db: DbDep):
    """The trades closed on a day, its net P&L, review and checklist completion."""
    filters = trade_service.TradeFilters(status="closed", date_from=day, date_to=day, by_close_date=True)
    trades, _ = trade_service.list_trades(db, filters, sort="close_time", descending=False)
    record = db.get(DailyReview, day)
    return {
        "date": day.isoformat(),
        "trades": trades,
        "pnl": round(sum(t.pnl or 0.0 for t in trades), 2),
        "review": reviews.review_fields(record) if record else None,
        "checklist_complete": day in reviews.checklist_completed_days(db, day, day),
    }
