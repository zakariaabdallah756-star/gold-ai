"""Account-level queries: settings, equity and the state of the trading day."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session

from ..models import AccountSettings, Trade, TradeEvent
from . import accounts, checklist
from .guardrails import DayState, daily_state, daily_violations

AccountIds = tuple[int, ...] | None  # trading accounts counted (services.accounts.Scope); None = every trade

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


def realized_pnl(
    db: Session, *, before: datetime | None = None, since: datetime | None = None, account_ids: AccountIds = None
) -> float:
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
        return accounts.filter_trades(stmt, account_ids)

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


def equity_at(db: Session, settings: AccountSettings, when: datetime, account_ids: AccountIds = None) -> float:
    start = accounts.starting_balance(db, settings, account_ids)
    return start + realized_pnl(db, before=when, account_ids=account_ids)


def booked_clause(since: datetime | None = None, before: datetime | None = None):
    """SQL condition: the trade has realised P&L booked within ``[since, before)``.

    Same rules as :func:`realized_pnl` and ``analytics.trade_bookings``: a closed
    unmanaged trade at its close time, a managed trade (open or closed) at any of
    its fills - its fees are booked on the closing fill.
    """

    def window(column) -> list:
        return [
            *([column >= since] if since is not None else []),
            *([column < before] if before is not None else []),
        ]

    fill_in_window = (
        select(TradeEvent.id)
        .where(TradeEvent.trade_id == Trade.id, TradeEvent.lots > 0, *window(TradeEvent.time))
        .exists()
    )
    return or_(
        and_(Trade.managed.is_(False), Trade.status == "closed", *window(CLOSE_TIME)),
        and_(Trade.managed.is_(True), fill_in_window),
    )


def report_base_equity(
    db: Session, settings: AccountSettings, date_from: date | None, account_ids: AccountIds = None
) -> float:
    """Equity a date-filtered report starts from: the account equity at the start of ``date_from``.

    Reports book P&L when it was realised, so everything booked before
    ``date_from`` (partial exits of trades closing later included) is in this
    base and nothing in the report counts it again.
    """
    if date_from is None:
        return accounts.starting_balance(db, settings, account_ids)
    return equity_at(db, settings, datetime.combine(date_from, time.min), account_ids)


def current_equity(db: Session, settings: AccountSettings, account_ids: AccountIds = None) -> float:
    return accounts.starting_balance(db, settings, account_ids) + realized_pnl(db, account_ids=account_ids)


def compute_day_state(
    db: Session, settings: AccountSettings, today: date | None = None, account_ids: AccountIds = None
) -> DayState:
    today = today or local_today(settings)
    start, end = day_bounds(today)
    start_equity = equity_at(db, settings, start, account_ids)

    # Money at risk on open positions: what the remainders lose at their current stops
    # (``risk_amount`` for rows that predate position management).
    risk = func.coalesce(Trade.remaining_risk, Trade.risk_amount, 0.0)
    open_risk, open_trades = db.execute(
        accounts.filter_trades(
            select(func.coalesce(func.sum(risk), 0.0), func.count(Trade.id)).where(Trade.status == "open"), account_ids
        )
    ).one()
    trades_today = db.scalar(
        accounts.filter_trades(
            select(func.count(Trade.id)).where(Trade.entry_time >= start, Trade.entry_time < end), account_ids
        )
    )

    return DayState(
        today=today,
        start_of_day_equity=start_equity,
        current_equity=current_equity(db, settings, account_ids),
        daily_loss_limit=max(start_equity, 0.0) * settings.max_daily_loss_pct / 100.0,
        realized_pnl_today=realized_pnl(db, since=start, before=end, account_ids=account_ids),
        open_risk=float(open_risk or 0.0),
        open_trades=int(open_trades or 0),
        trades_today=int(trades_today or 0),
        checklist_complete=checklist.is_complete(db, today),
    )


STATE_RANK = {"ok": 0, "warning": 1, "locked": 2, "breached": 3}
SHARED_DAILY_CODES = ("CHECKLIST_INCOMPLETE",)


def daily_status(db: Session, settings: AccountSettings, account_id=accounts.ACTIVE) -> dict:
    """Everything the UI needs to render the discipline status bar, for the account shown (or one account).

    With every account shown, each account is judged on its own (its own daily loss limit and trade
    count): the totals add up, the state is the worst account's and account-specific reasons name it.
    """
    from . import context_rules, prop_rules  # local import: both may use this module

    scope = accounts.resolve_scope(db, settings, account_id)
    now = local_now(settings)
    extras = context_rules.status_extras(db, settings, now)
    prop_extras = prop_rules.status_extras(db, settings, now)
    shared = list(extras.pop("reasons", [])) + list(prop_extras.pop("reasons", []))
    extras.update(prop_extras)

    if scope.is_all and len(scope.accounts) > 1:
        parts = [(a, compute_day_state(db, settings, account_ids=(a.id,))) for a in scope.accounts]
        day = parts[0][1]
        own, once = [], {}
        for account_row, part in parts:
            name = settings.account_name if accounts.is_default(settings, account_row) else account_row.name
            for violation in daily_violations(settings, part):
                if violation.code in SHARED_DAILY_CODES:  # the checklist is one for the whole day
                    once.setdefault(violation.code, violation)
                else:
                    own.append(violation.named(name))
        reasons = list(once.values()) + own + shared
        state = max((daily_state(settings, part) for _a, part in parts), key=lambda value: STATE_RANK.get(value, 0))
        totals = {
            key: round(sum(getattr(part, key) for _a, part in parts), 2)
            for key in (
                "current_equity",
                "start_of_day_equity",
                "daily_loss_limit",
                "realized_pnl_today",
                "loss_used",
                "remaining_loss_budget",
                "open_risk",
            )
        }
        totals["open_trades"] = sum(part.open_trades for _a, part in parts)
        totals["trades_today"] = sum(part.trades_today for _a, part in parts)
        totals["loss_used_ratio"] = max(part.loss_used_ratio for _a, part in parts)
        max_trades = 0  # a per-account limit: breaches are listed per account
    else:
        day = compute_day_state(db, settings, account_ids=scope.account_ids)
        reasons = daily_violations(settings, day) + shared
        state = daily_state(settings, day)
        totals = {
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
        }
        max_trades = settings.max_trades_per_day
    currency = settings.currency
    if scope.account is not None and not accounts.is_default(settings, scope.account):
        currency = scope.account.currency
    return {
        **extras,
        **totals,
        "today": day.today,
        "currency": currency,
        "starting_balance": accounts.starting_balance(db, settings, scope.account_ids),
        "max_trades_per_day": max_trades,
        "checklist_complete": day.checklist_complete,
        "checklist_required": settings.require_checklist,
        "state": state,
        "can_trade": not any(reason.severity == "block" for reason in reasons),
        "reasons": [reason.to_dict() for reason in reasons],
        "account_id": scope.account.id if scope.account else None,
        "accounts": [a.id for a in scope.accounts],
    }
