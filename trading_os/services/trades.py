"""Trade lifecycle: guardrail evaluation, derived metrics and CRUD."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import Select, String, cast, func, or_, select
from sqlalchemy.orm import Session, selectinload

from ..models import AccountSettings, Market, Strategy, Trade, TradeTarget, TradingSession
from ..schemas import TradeIn
from . import context_rules, positions, uploads
from .account import CLOSE_TIME, compute_day_state, equity_at, local_now, local_today
from .guardrails import (
    DAILY_CODES,
    Violation,
    daily_violations,
    is_blocking,
    trade_rule_violations,
)
from .sessions import classify_session, in_trading_window
from .sizing import blended_rr, conversion_hint, executed_rr, gross_pnl, planned_rr, risk_for_lots

# Warnings that should not, on their own, mark a trade as "rules broken".
NON_COMPLIANCE_EXEMPT = frozenset({"NO_TAKE_PROFIT"})

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


def outcome_for(pnl: float) -> str:
    rounded = round(pnl, 2)
    if rounded > 0:
        return "win"
    if rounded < 0:
        return "loss"
    return "breakeven"


def evaluate(
    db: Session,
    settings: AccountSettings,
    data: TradeIn,
    existing: Trade | None = None,
) -> Evaluation:
    """Compute derived inputs and every guardrail violation for ``data``.

    A trade is *live* when it is a new open position dated today or later;
    only live trades can be blocked. Daily rules are evaluated for new trades
    dated today; on updates the originally recorded daily violations are kept.
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

    today = local_today(settings)
    if existing is None:
        if entry_time.date() >= today:
            day = compute_day_state(db, settings, today)
            violations += daily_violations(settings, day, risk_amount)
        # Economic-calendar window and kill switch (services.context_rules).
        violations += context_rules.entry_violations(
            db, settings, symbol=data.symbol, entry_time=entry_time, now=local_now(settings)
        )
    else:
        violations += [Violation(**v) for v in existing.violations or [] if v.get("code") in DAILY_CODES]

    live = existing is None and data.status == "open" and entry_time.date() >= today
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


def _apply(trade: Trade, data: TradeIn, ev: Evaluation, settings: AccountSettings) -> None:
    trade.status = data.status
    trade.entry_time = ev.entry_time
    trade.exit_time = ev.exit_time if data.status == "closed" else None
    trade.symbol = data.symbol
    trade.direction = data.direction
    trade.entry_price = data.entry_price
    trade.stop_loss = data.stop_loss
    trade.take_profit = data.take_profit
    trade.exit_price = data.exit_price if data.status == "closed" else None
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

    if data.status != "closed":
        trade.pnl = trade.executed_rr = trade.r_multiple = trade.outcome = None
        trade.pnl_manual = False
        positions.recompute(trade, ev.market, settings)
        return

    if data.pnl is not None:
        pnl, manual = data.pnl, True
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
    positions.recompute(trade, ev.market, settings)


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
    ev = evaluate(db, settings, data, existing=trade)
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
    """Closed trades in chronological close order - the input of analytics."""
    filters = replace(filters, status="closed", by_close_date=True)
    stmt = apply_filters(select(Trade), filters).order_by(CLOSE_TIME.asc(), Trade.id.asc())
    return list(db.scalars(stmt))
