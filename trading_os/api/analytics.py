"""Statistical dashboard, P&L calendar and day-detail endpoints."""

from datetime import date, datetime
from typing import Annotated

from fastapi import APIRouter, Query
from sqlalchemy import func, select

from ..models import DailyReview, Trade
from ..schemas import FiniteModel, TradeOut
from ..services import accounts, analytics, reviews, time_analytics
from ..services import trades as trade_service
from ..services.account import local_today
from .deps import DbDep, ScopeDep, SettingsDep, scope_currency
from .trades import FiltersDep

router = APIRouter(prefix="/api", tags=["analytics"])

MonthParam = Annotated[str | None, Query(pattern=r"^[12]\d{3}-(0[1-9]|1[0-2])$", description="YYYY-MM")]


class DayFillOut(FiniteModel):
    """P&L booked on the day: one fill of a managed trade, or a whole trade closed without fills."""

    trade_id: int
    time: datetime
    kind: str  # tp | partial | stop | close
    target_level: int | None
    lots: float | None
    price: float | None
    pnl: float  # booked amount; the closing fill is net of the trade's fees
    closes_trade: bool
    symbol: str
    direction: str
    r_multiple: float | None  # the trade's R, on the closing fill only
    trade_pnl: float | None  # the trade's whole net P&L once closed
    trade_status: str
    trade_exit_time: datetime | None


class DayStatsOut(FiniteModel):
    trades: int  # trades closed that day
    wins: int
    losses: int
    win_rate: float | None
    partials: int  # fills that did not close their trade


class DayDetailOut(FiniteModel):
    date: str
    trades: list[TradeOut]
    fills: list[DayFillOut]
    pnl: float
    stats: DayStatsOut
    review: dict | None
    checklist_complete: bool


@router.get("/analytics")
def read_analytics(settings: SettingsDep, db: DbDep, filters: FiltersDep, scope: ScopeDep):
    """Metrics, equity curve, daily P&L, R distribution, breakdowns and the time-of-day heatmap.

    Trade statistics cover the trades *closed* in the date range. Daily P&L,
    the equity curve and the drawdown follow the P&L *booked* in the range -
    each partial exit on its fill day, open trades included - starting from
    the real account equity at the start date, so drawdown % stays meaningful.
    """
    trades, report = trade_service.performance_report(db, settings, filters)
    report["time_heatmap"] = time_analytics.heatmap(trades)
    report["currency"] = scope_currency(settings, scope)
    open_trades = accounts.filter_trades(select(func.count(Trade.id)).where(Trade.status == "open"), scope.account_ids)
    report["open_trades"] = db.scalar(open_trades) or 0
    return report


@router.get("/analytics/calendar")
def read_calendar(settings: SettingsDep, db: DbDep, scope: ScopeDep, month: MonthParam = None):
    """P&L calendar of a month (default: the current account-local month), by the day P&L was booked."""
    first = time_analytics.parse_month(month) if month else local_today(settings).replace(day=1)
    _, _, start, end = time_analytics.month_grid(first)
    report = time_analytics.calendar_month(
        reviews.booked_between(db, start, end, account_ids=scope.account_ids),
        first,
        reviewed=reviews.reviewed_days(db, start, end),
        checklist_complete=reviews.checklist_completed_days(db, start, end),
    )
    report["currency"] = scope_currency(settings, scope)
    return report


def _fill(booking: analytics.Booking) -> dict:
    trade = booking.trade
    return {
        "trade_id": trade.id,
        "time": booking.time,
        "kind": booking.kind,
        "target_level": booking.target_level,
        "lots": booking.lots,
        "price": booking.price,
        "pnl": round(booking.amount, 2),
        "closes_trade": booking.closes,
        "symbol": trade.symbol,
        "direction": trade.direction,
        "r_multiple": trade.r_multiple if booking.closes else None,
        "trade_pnl": trade.pnl if trade.status == "closed" else None,
        "trade_status": trade.status,
        "trade_exit_time": trade.exit_time if trade.status == "closed" else None,
    }


@router.get("/analytics/day/{day}", response_model=DayDetailOut)
def read_day(day: date, db: DbDep, scope: ScopeDep):
    """The P&L booked on a day (each fill, partial exits included), the trades behind it, review and checklist."""
    trades = reviews.booked_between(db, day, day, load=trade_service.TRADE_LOAD_OPTIONS, account_ids=scope.account_ids)
    booked = reviews.bookings_between(trades, day, day)
    first_booking: dict[int, datetime] = {}
    for booking in booked:
        first_booking.setdefault(booking.trade.id, booking.time)
    trades = sorted((t for t in trades if t.id in first_booking), key=lambda t: (first_booking[t.id], t.id))
    stats = analytics.booking_stats(booked)
    record = db.get(DailyReview, day)
    return {
        "date": day.isoformat(),
        "trades": trades,
        "fills": [_fill(booking) for booking in booked],
        "pnl": stats.pop("pnl"),
        "stats": stats,
        "review": reviews.review_fields(record) if record else None,
        "checklist_complete": day in reviews.checklist_completed_days(db, day, day),
    }
