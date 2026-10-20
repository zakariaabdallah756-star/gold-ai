"""Account-level queries: settings, equity and the state of the trading day."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..models import AccountSettings, Trade, TradeEvent
from . import checklist
from .guardrails import DayState, daily_state, daily_violations

CLOSE_TIME = func.coalesce(Trade.exit_time, Trade.entry_time)


def get_settings(db: Session) -> AccountSettings:
    settings = db.get(AccountSettings, 1)
    if settings is None:
        settings = AccountSettings(id=1)
        db.add(settings)
        db.commit()
    return settings


def local_now(settings: AccountSettings) -> datetime:
    """Current wall-clock time in the account time zone (naive)."""
    return datetime.now(ZoneInfo(settings.timezone)).replace(tzinfo=None, microsecond=0)


def local_today(settings: AccountSettings) -> date:
    return local_now(settings).date()


def day_bounds(day: date) -> tuple[datetime, datetime]:
    start = datetime.combine(day, time.min)
    return start, start + timedelta(days=1)


def realized_pnl(db: Session, *, before: datetime | None = None, since: datetime | None = None) -> float:
    """Net realised P&L, attributed to the moment it was realised, within ``[since, before)``.

    Follows the position ledger: closed *unmanaged* trades count their net P&L
    at their close time; *managed* trades (open or closed) count each fill's
    P&L at the fill time - so partial profits hit equity and the daily loss
    limit immediately - and their fees when the trade closes.
    """

    def window(stmt, column):
        if before is not None:
            stmt = stmt.where(column < before)
        if since is not None:
            stmt = stmt.where(column >= since)
        return stmt

    legacy = window(
        select(func.coalesce(func.sum(Trade.pnl), 0.0)).where(Trade.status == "closed", Trade.managed.is_(False)),
        CLOSE_TIME,
    )
    fills = window(
        select(func.coalesce(func.sum(TradeEvent.pnl), 0.0))
        .join(Trade, TradeEvent.trade_id == Trade.id)
        .where(Trade.managed.is_(True), TradeEvent.lots > 0),
        TradeEvent.time,
    )
    fees = window(
        select(func.coalesce(func.sum(Trade.fees), 0.0)).where(Trade.status == "closed", Trade.managed.is_(True)),
        CLOSE_TIME,
    )
    total = db.scalar(select(legacy.scalar_subquery() + fills.scalar_subquery() - fees.scalar_subquery()))
    return round(float(total or 0.0), 2)


def equity_at(db: Session, settings: AccountSettings, when: datetime) -> float:
    return settings.starting_balance + realized_pnl(db, before=when)


def current_equity(db: Session, settings: AccountSettings) -> float:
    return settings.starting_balance + realized_pnl(db)


def compute_day_state(db: Session, settings: AccountSettings, today: date | None = None) -> DayState:
    today = today or local_today(settings)
    start, end = day_bounds(today)
    start_equity = equity_at(db, settings, start)

    # Money at risk on open positions: what the remainders lose at their current stops
    # (``risk_amount`` for rows that predate position management).
    risk = func.coalesce(Trade.remaining_risk, Trade.risk_amount, 0.0)
    open_risk, open_trades = db.execute(
        select(func.coalesce(func.sum(risk), 0.0), func.count(Trade.id)).where(Trade.status == "open")
    ).one()
    trades_today = db.scalar(select(func.count(Trade.id)).where(Trade.entry_time >= start, Trade.entry_time < end))

    return DayState(
        today=today,
        start_of_day_equity=start_equity,
        current_equity=current_equity(db, settings),
        daily_loss_limit=max(start_equity, 0.0) * settings.max_daily_loss_pct / 100.0,
        realized_pnl_today=realized_pnl(db, since=start, before=end),
        open_risk=float(open_risk or 0.0),
        open_trades=int(open_trades or 0),
        trades_today=int(trades_today or 0),
        checklist_complete=checklist.is_complete(db, today),
    )


def daily_status(db: Session, settings: AccountSettings) -> dict:
    """Everything the UI needs to render the discipline status bar."""
    from . import context_rules, prop_rules  # local import: both may use this module

    day = compute_day_state(db, settings)
    now = local_now(settings)
    extras = context_rules.status_extras(db, settings, now)
    prop_extras = prop_rules.status_extras(db, settings, now)
    reasons = daily_violations(settings, day) + list(extras.pop("reasons", [])) + list(prop_extras.pop("reasons", []))
    extras.update(prop_extras)
    return {
        **extras,
        "today": day.today,
        "currency": settings.currency,
        "starting_balance": settings.starting_balance,
        "current_equity": day.current_equity,
        "start_of_day_equity": day.start_of_day_equity,
        "daily_loss_limit": day.daily_loss_limit,
        "realized_pnl_today": day.realized_pnl_today,
        "loss_used": day.loss_used,
        "loss_used_ratio": day.loss_used_ratio,
        "remaining_loss_budget": day.remaining_loss_budget,
        "open_risk": day.open_risk,
        "open_trades": day.open_trades,
        "trades_today": day.trades_today,
        "max_trades_per_day": settings.max_trades_per_day,
        "checklist_complete": day.checklist_complete,
        "checklist_required": settings.require_checklist,
        "state": daily_state(settings, day),
        "can_trade": not any(reason.severity == "block" for reason in reasons),
        "reasons": [reason.to_dict() for reason in reasons],
    }
