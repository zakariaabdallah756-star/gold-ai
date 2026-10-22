"""Trade lifecycle: guardrail evaluation, derived metrics and CRUD."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import Select, String, cast, func, or_, select
from sqlalchemy.orm import Session, selectinload

from ..models import AccountSettings, ChecklistDay, Market, Strategy, Trade, TradeTarget, TradingSession
from ..schemas import TradeIn
from . import analytics, context_rules, positions, prop_rules, uploads
from .account import (
    CLOSE_TIME,
    booked_clause,
    compute_day_state,
    day_bounds,
    equity_at,
    local_now,
    local_today,
    realized_pnl,
    report_base_equity,
)
from .copilot import TradeContext, strategy_violations
from .guardrails import (
    DAILY_CODES,
    DayState,
    Violation,
    daily_violations,
    is_blocking,
    trade_rule_violations,
)
from .position_engine import outcome_for
from .sessions import classify_session, in_trading_window, open_sessions
from .sizing import blended_rr, conversion_hint, executed_rr, gross_pnl, planned_rr, risk_for_lots

# Warnings that should not, on their own, mark a trade as "rules broken" (informational notices).
NON_COMPLIANCE_EXEMPT = frozenset(
    {"NO_TAKE_PROFIT", "STRATEGY_INCUBATOR", "PROP_TARGET_REACHED", "PROP_CHALLENGE_ENDED"}
)
# Fields that define a trade for the guardrails: editing only other fields (notes, tags, emotion,
# mistakes, screenshots...) keeps the violations recorded when the trade was logged.
MATERIAL_FIELDS = (
    "entry_time",
    "symbol",
    "direction",
    "entry_price",
    "stop_loss",
    "take_profit",
    "position_size",
    "strategy",
    "timeframe",
    "session",
    "status",
)

SORTABLE = {
    "entry_time": Trade.entry_time,
    "close_time": CLOSE_TIME,
    "symbol": Trade.symbol,
    "pnl": Trade.pnl,
    "r_multiple": Trade.r_multiple,
    "planned_rr": Trade.planned_rr,
}


class TradeBlocked(Exception):
    """A live entry was refused by one or more blocking guardrails."""

    def __init__(self, violations: list[Violation]):
        super().__init__("; ".join(v.message for v in violations if v.severity == "block"))
        self.violations = violations


class TradeError(ValueError):
    """The trade data is consistent but cannot be processed (e.g. P&L unknown)."""


@dataclass
class Evaluation:
    entry_time: datetime
    exit_time: datetime | None
    market: Market | None
    risk_amount: float | None
    risk_pct: float | None
    planned_rr: float | None
    session: str
    live: bool
    violations: list[Violation] = field(default_factory=list)

    @property
    def blocked(self) -> bool:
        return self.live and is_blocking(self.violations)


def normalize_time(value: datetime | None, tz_name: str) -> datetime | None:
    """Convert aware datetimes to naive account-local time; drop sub-seconds."""
    if value is None:
        return None
    if value.tzinfo is not None:
        value = value.astimezone(ZoneInfo(tz_name)).replace(tzinfo=None)
    return value.replace(microsecond=0)


def evaluate(
    db: Session,
    settings: AccountSettings,
    data: TradeIn,
    existing: Trade | None = None,
) -> Evaluation:
    """Compute derived inputs and every guardrail violation for ``data``.

    A trade is *live* when it is an open position dated today or later that is
    new, reopened by the edit, or had its risk raised by the edit; only live
    trades can be blocked. Daily rules judge live entries against the current
    day and trades logged after the fact against the day as it stood at their
    entry time. Other edits keep the daily violations recorded at logging time
    (the news window follows a corrected entry time), and edits that change no
    trade-defining field keep every recorded violation.
    """
    tz = settings.timezone
    entry_time = normalize_time(data.entry_time, tz)
    exit_time = normalize_time(data.exit_time, tz)
    if exit_time is not None and exit_time < entry_time:
        raise TradeError("Exit time cannot be before entry time")

    market = db.scalar(select(Market).where(Market.symbol == data.symbol))
    risk_amount = data.risk_amount
    if risk_amount is None and data.position_size and market is not None:
        computed = risk_for_lots(market, settings.currency, data.entry_price, data.stop_loss, data.position_size)
        risk_amount = round(computed, 2) if computed is not None else None

    windows = list(db.scalars(select(TradingSession)))
    session_name = data.session or classify_session(entry_time, tz, windows)
    if existing is not None and entry_time != existing.entry_time and data.session == existing.session:
        # The form sends the stored session back: a corrected entry time is re-classified.
        session_name = classify_session(entry_time, tz, windows)
    if data.targets:
        rr = blended_rr(data.entry_price, data.stop_loss, [(t.price, t.close_pct) for t in data.targets])
    else:
        rr = planned_rr(data.entry_price, data.stop_loss, data.take_profit)
    equity = equity_at(db, settings, entry_time)
    strategy = db.scalar(select(Strategy).where(Strategy.name == data.strategy)) if data.strategy else None

    violations = trade_rule_violations(
        settings,
        planned_rr=rr,
        has_take_profit=data.take_profit is not None,
        risk_amount=risk_amount,
        equity=equity,
        confluence_count=len(data.confluences),
        strategy_name=data.strategy,
        strategy_min_confluences=strategy.min_confluences if strategy else 0,
        in_trading_window=in_trading_window(entry_time, tz, windows),
    )
    # Strategy Co-Pilot: every parameter of the chosen strategy must be met.
    checklist_day = db.get(ChecklistDay, entry_time.date())
    violations += strategy_violations(
        settings,
        strategy,
        TradeContext(
            direction=data.direction,
            session=session_name,
            timeframe=data.timeframe,
            confluences=list(data.confluences),
            planned_rr=rr,
            bias=checklist_day.bias if checklist_day else "",
            open_sessions=open_sessions(entry_time, tz, windows),
        ),
    )

    today = local_today(settings)
    now = local_now(settings)
    managed = existing is not None and positions.has_fills(existing)
    # An edit that reopens a trade, or raises the risk of an open one, is judged like a new entry.
    reopening = existing is not None and not managed and existing.status != "open" and data.status == "open"
    risk_raised = (
        existing is not None
        and not managed
        and existing.status == "open"
        and (risk_amount or 0.0) > (existing.risk_amount or 0.0) + 0.005
    )
    entry_like = existing is None or reopening or risk_raised
    live = entry_like and data.status == "open" and entry_time.date() >= today

    if live:
        day, own_risk = _live_day_state(db, settings, today, existing)
        new_risk = max((risk_amount or 0.0) - own_risk, 0.0) if risk_amount is not None else None
        violations += daily_violations(settings, day, new_risk)
        # Prop-firm challenge rules ("prop mode", services.prop_rules).
        violations += prop_rules.entry_violations(db, settings, risk_amount=new_risk, entry_time=entry_time, now=now)
    elif entry_like and entry_time.date() == today:
        # Logged after the fact: judge the day as it stood when the trade was entered.
        violations += daily_violations(settings, _day_state_at(db, settings, entry_time, existing))
    if entry_like:
        # Economic-calendar window and kill switch (services.context_rules).
        violations += context_rules.entry_violations(db, settings, symbol=data.symbol, entry_time=entry_time, now=now)
    else:
        # Keep what was recorded when the trade was logged, except the news window, which follows
        # the (possibly corrected) entry time and symbol.
        violations += [
            Violation(**v)
            for v in existing.violations or []
            if v.get("code") in DAILY_CODES and v.get("code") != "NEWS_WINDOW"
        ]
        violations += [
            v
            for v in context_rules.entry_violations(db, settings, symbol=data.symbol, entry_time=entry_time, now=now)
            if v.code == "NEWS_WINDOW"
        ]
        if not _material_change(existing, data, entry_time, session_name):
            violations = [Violation(**v) for v in existing.violations or []]

    if not live:
        violations = [violation.as_warning() for violation in violations]

    return Evaluation(
        entry_time=entry_time,
        exit_time=exit_time,
        market=market,
        risk_amount=risk_amount,
        risk_pct=(risk_amount / equity * 100) if risk_amount and equity > 0 else None,
        planned_rr=rr,
        session=session_name,
        live=live,
        violations=violations,
    )


def _live_day_state(
    db: Session, settings: AccountSettings, today: date, existing: Trade | None
) -> tuple[DayState, float]:
    """Today's state for a live entry, without the edited trade's own count and open risk."""
    day = compute_day_state(db, settings, today)
    own_risk = 0.0
    if existing is not None:
        if existing.entry_time.date() == today:
            day = replace(day, trades_today=max(day.trades_today - 1, 0))
        if existing.status == "open":
            own_risk = existing.remaining_risk if existing.remaining_risk is not None else existing.risk_amount or 0.0
            day = replace(day, open_risk=max(day.open_risk - own_risk, 0.0))
    return day, own_risk


def _day_state_at(db: Session, settings: AccountSettings, when: datetime, existing: Trade | None) -> DayState:
    """The day as it stood at ``when``: trades entered before it and P&L realised before it.

    Open risk at that moment is unknown, so the daily risk budget is not judged.
    """
    start, _ = day_bounds(when.date())
    # Inclusive of the same second: a loss closed as this trade was entered came first.
    earlier = select(func.count(Trade.id)).where(Trade.entry_time >= start, Trade.entry_time <= when)
    if existing is not None:
        earlier = earlier.where(Trade.id != existing.id)
    day = compute_day_state(db, settings, when.date())
    return replace(
        day,
        realized_pnl_today=realized_pnl(db, since=start, before=when + timedelta(seconds=1)),
        trades_today=int(db.scalar(earlier) or 0),
        open_risk=0.0,
    )


def _material_change(existing: Trade, data: TradeIn, entry_time: datetime, session: str) -> bool:
    """Whether an edit touches a field the guardrails judge (see ``MATERIAL_FIELDS``)."""
    proposed = {**data.model_dump(include=set(MATERIAL_FIELDS)), "entry_time": entry_time, "session": session}
    if any(getattr(existing, key) != proposed[key] for key in MATERIAL_FIELDS):
        return True
    if sorted(existing.confluences or []) != sorted(data.confluences):
        return True
    return [(t.price, t.close_pct) for t in existing.targets] != [(t.price, t.close_pct) for t in data.targets]


def _apply(trade: Trade, data: TradeIn, ev: Evaluation, settings: AccountSettings) -> None:
    # A managed trade (with fills) derives status, exit and P&L from its ledger.
    managed = positions.has_fills(trade)
    # The recorded auto P&L, for keeping it when the market has since been removed from the plan.
    recorded_gross = (
        trade.pnl + (trade.fees or 0.0)
        if trade.id is not None
        and trade.pnl is not None
        and not trade.pnl_manual
        and (trade.symbol, trade.direction, trade.entry_price, trade.exit_price, trade.position_size)
        == (data.symbol, data.direction, data.entry_price, data.exit_price, data.position_size)
        else None
    )
    if not managed:
        trade.status = data.status
        trade.exit_time = ev.exit_time if data.status == "closed" else None
        trade.exit_price = data.exit_price if data.status == "closed" else None
    trade.entry_time = ev.entry_time
    trade.symbol = data.symbol
    trade.direction = data.direction
    trade.entry_price = data.entry_price
    trade.stop_loss = data.stop_loss
    trade.take_profit = data.take_profit
    trade.position_size = data.position_size
    trade.risk_amount = ev.risk_amount
    trade.fees = data.fees
    trade.strategy = data.strategy
    trade.timeframe = data.timeframe
    trade.session = ev.session
    trade.confluences = list(data.confluences)
    trade.emotion = data.emotion
    trade.tags = list(data.tags)
    trade.mistakes = list(data.mistakes)
    trade.notes = data.notes
    trade.source = data.source
    trade.signal_text = data.signal_text
    trade.be_enabled = data.be_enabled
    trade.be_offset_pips = data.be_offset_pips
    trade.trail_enabled = data.trail_enabled
    trade.trail_distance_pips = data.trail_distance_pips
    trade.trail_trigger = data.trail_trigger
    _sync_targets(trade, data)
    trade.planned_rr = round(ev.planned_rr, 4) if ev.planned_rr is not None else None
    trade.violations = [violation.to_dict() for violation in ev.violations]
    trade.rule_compliant = (
        data.rule_compliant
        if data.rule_compliant is not None
        else not any(v.code not in NON_COMPLIANCE_EXEMPT for v in ev.violations)
    )

    if managed:
        _recompute(trade, ev.market, settings)
        return
    if data.status != "closed":
        trade.pnl = trade.executed_rr = trade.r_multiple = trade.outcome = None
        trade.pnl_manual = False
        _recompute(trade, ev.market, settings)
        return

    if data.pnl is not None:
        pnl, manual = data.pnl, True
    elif ev.market is None and recorded_gross is not None:
        pnl, manual = recorded_gross - data.fees, False  # same fill, market no longer configured
    else:
        if ev.market is None or not data.position_size:
            raise TradeError(
                "Enter the net P&L, or set a position size on a configured market so it can be auto-calculated."
            )
        gross = gross_pnl(
            ev.market, settings.currency, data.direction, data.entry_price, data.exit_price, data.position_size
        )
        if gross is None:
            raise TradeError(conversion_hint(ev.market, settings.currency) + " - or enter the net P&L manually.")
        pnl, manual = gross - data.fees, False

    rr = executed_rr(data.direction, data.entry_price, data.stop_loss, data.exit_price)
    trade.pnl = round(pnl, 2)
    trade.pnl_manual = manual
    trade.executed_rr = round(rr, 4) if rr is not None else None
    r_multiple = trade.pnl / ev.risk_amount if ev.risk_amount else rr
    trade.r_multiple = round(r_multiple, 4) if r_multiple is not None else None
    trade.outcome = outcome_for(trade.pnl)
    _recompute(trade, ev.market, settings)


def _recompute(trade: Trade, market: Market | None, settings: AccountSettings) -> None:
    try:
        positions.recompute(trade, market, settings)
    except positions.PositionError as exc:
        raise TradeError(str(exc)) from exc


def _sync_targets(trade: Trade, data: TradeIn) -> None:
    wanted = [(t.price, t.close_pct) for t in data.targets]
    if [(t.price, t.close_pct) for t in trade.targets] != wanted:
        trade.targets = [
            TradeTarget(level=level, price=price, close_pct=close_pct)
            for level, (price, close_pct) in enumerate(wanted, start=1)
        ]


def create_trade(db: Session, settings: AccountSettings, data: TradeIn) -> tuple[Trade, list[Violation]]:
    ev = evaluate(db, settings, data)
    if ev.blocked:
        raise TradeBlocked(ev.violations)
    trade = Trade()
    _apply(trade, data, ev, settings)
    db.add(trade)
    db.commit()
    return get_trade(db, trade.id), ev.violations


def update_trade(db: Session, settings: AccountSettings, trade: Trade, data: TradeIn) -> tuple[Trade, list[Violation]]:
    """Save edits; a managed trade ignores status/exit/P&L from ``data``.

    Edits are only blocked when they act like a new live entry: reopening a trade
    dated today, or raising the risk of today's open trade.
    """
    ev = evaluate(db, settings, data, existing=trade)
    if ev.blocked:
        raise TradeBlocked(ev.violations)
    try:
        positions.validate_edit(
            trade,
            ev.market,
            settings,
            entry_time=ev.entry_time,
            symbol=data.symbol,
            direction=data.direction,
            entry_price=data.entry_price,
            position_size=data.position_size,
        )
    except positions.PositionError as exc:
        raise TradeError(str(exc)) from exc
    _apply(trade, data, ev, settings)
    db.commit()
    return get_trade(db, trade.id), ev.violations


def delete_trade(db: Session, uploads_dir, trade: Trade) -> None:
    filenames = [shot.filename for shot in trade.screenshots if shot.kind == "file" and shot.filename]
    db.delete(trade)
    db.commit()
    for filename in filenames:
        uploads.delete_file(uploads_dir, filename)


TRADE_LOAD_OPTIONS = (selectinload(Trade.screenshots), selectinload(Trade.targets), selectinload(Trade.events))


def get_trade(db: Session, trade_id: int) -> Trade | None:
    stmt = select(Trade).options(*TRADE_LOAD_OPTIONS).where(Trade.id == trade_id)
    return db.scalar(stmt.execution_options(populate_existing=True))


@dataclass
class TradeFilters:
    status: str | None = None
    symbol: str | None = None
    strategy: str | None = None
    session: str | None = None
    direction: str | None = None
    outcome: str | None = None
    emotion: str | None = None
    date_from: date | None = None
    date_to: date | None = None
    q: str | None = None
    by_close_date: bool = False
    strategy_names: tuple[str, ...] = ()  # any of these strategy names (a strategy and its former names)


def apply_filters(stmt: Select, filters: TradeFilters) -> Select:
    for column, value in (
        (Trade.status, filters.status),
        (Trade.symbol, filters.symbol),
        (Trade.strategy, filters.strategy),
        (Trade.session, filters.session),
        (Trade.direction, filters.direction),
        (Trade.outcome, filters.outcome),
        (Trade.emotion, filters.emotion),
    ):
        if value:
            stmt = stmt.where(column == value)
    if filters.strategy_names:
        stmt = stmt.where(Trade.strategy.in_(filters.strategy_names))

    time_column = CLOSE_TIME if filters.by_close_date else Trade.entry_time
    if filters.date_from:
        stmt = stmt.where(time_column >= datetime.combine(filters.date_from, datetime.min.time()))
    if filters.date_to:
        stmt = stmt.where(time_column < datetime.combine(filters.date_to + timedelta(days=1), datetime.min.time()))
    if filters.q:
        pattern = f"%{filters.q.strip()}%"
        stmt = stmt.where(
            or_(
                Trade.notes.ilike(pattern),
                Trade.symbol.ilike(pattern),
                Trade.strategy.ilike(pattern),
                cast(Trade.tags, String).ilike(pattern),
            )
        )
    return stmt


def list_trades(
    db: Session,
    filters: TradeFilters,
    *,
    sort: str = "entry_time",
    descending: bool = True,
    limit: int | None = None,
    offset: int = 0,
) -> tuple[list[Trade], int]:
    base = apply_filters(select(Trade), filters)
    total = db.scalar(select(func.count()).select_from(base.subquery())) or 0
    column = SORTABLE.get(sort, Trade.entry_time)
    order = (column.desc(), Trade.id.desc()) if descending else (column.asc(), Trade.id.asc())
    stmt = base.options(*TRADE_LOAD_OPTIONS).order_by(*order).offset(offset)
    if limit:
        stmt = stmt.limit(limit)
    return list(db.scalars(stmt)), total


def closed_trades(db: Session, filters: TradeFilters) -> list[Trade]:
    """Closed trades in chronological close order - the input of the trade statistics."""
    filters = replace(filters, status="closed", by_close_date=True)
    stmt = apply_filters(select(Trade), filters).options(selectinload(Trade.events))
    return list(db.scalars(stmt.order_by(CLOSE_TIME.asc(), Trade.id.asc())))


def report_window(filters: TradeFilters) -> tuple[datetime | None, datetime | None]:
    """``[since, before)`` covering the filters' whole days (``None`` = open-ended)."""
    since = datetime.combine(filters.date_from, datetime.min.time()) if filters.date_from else None
    before = datetime.combine(filters.date_to + timedelta(days=1), datetime.min.time()) if filters.date_to else None
    return since, before


def booked_trades(db: Session, filters: TradeFilters) -> list[Trade]:
    """Trades (open or closed) with P&L booked in the filters' date window, with their fills loaded.

    The other filters (asset, strategy, ...) apply as usual; the status filter does not.
    """
    since, before = report_window(filters)
    rest = replace(filters, status=None, date_from=None, date_to=None, by_close_date=False)
    stmt = apply_filters(select(Trade), rest).where(booked_clause(since, before)).options(selectinload(Trade.events))
    return list(db.scalars(stmt.order_by(CLOSE_TIME.asc(), Trade.id.asc())))


def report_bookings(db: Session, filters: TradeFilters) -> list[analytics.Booking]:
    """The bookings (see ``analytics.bookings``) inside the filters' date window."""
    since, before = report_window(filters)
    return analytics.bookings(booked_trades(db, filters), since=since, before=before)


def performance_report(db: Session, settings: AccountSettings, filters: TradeFilters) -> tuple[list[Trade], dict]:
    """The journal's performance report for ``filters`` and the closed trades it is built from.

    Trade statistics cover the trades *closed* in the date range, each with its
    whole P&L. Daily P&L and the equity curve follow the P&L *booked* in the
    range - partial exits on their fill day, open trades included - starting
    from the account equity at the start of the range.
    """
    trades = closed_trades(db, filters)
    base_equity = report_base_equity(db, settings, filters.date_from)
    return trades, analytics.full_report(trades, base_equity, report_bookings(db, filters))
