"""Prop-firm challenge tracking: presets, metrics from the realised ledger, pass/fail evaluation.

All money figures come from the same realised-P&L rules as the account
(``services.account.realized_pnl``): closed unmanaged trades count at their
close time, managed trades count each fill at its fill time and their fees
when they close. A challenge only sees P&L realised on or after its start
date (00:00 account time).

Rules, all as a percentage of the *initial* account size:

* **Daily loss limit** = ``max_daily_loss_pct`` of the account size. Today's
  loss is the realised loss of the day (never negative). The remaining daily
  room also reserves the open risk of every open position.
* **Max drawdown floor**: *static* = account size × (1 − ``max_total_drawdown_pct``);
  *trailing* = highest end-of-day balance (completed days, starting at the
  account size) − ``max_total_drawdown_pct`` of the account size, capped at the
  account size (once the floor reaches the initial balance it stops trailing).
* **Profit target** = ``profit_target_pct`` of the account size (funded
  accounts have none).

A challenge *fails* when a day's loss reaches the daily limit or an
end-of-day (or the current) balance is at/below the floor, or when its time
limit elapses without a pass. It *passes* when the target is reached and the
minimum number of trading days (distinct entry dates) is met.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..models import AccountSettings, PropChallenge, Trade, TradeEvent
from .account import CLOSE_TIME, compute_day_state, local_today

PHASES = ("phase1", "phase2", "funded", "custom")
DRAWDOWN_MODES = ("static", "trailing")
STATUSES = ("active", "passed", "failed", "archived")
VERIFY_NOTE = "Verify the current rules on the firm's website - firms change their rules."

# Programmes whose headline rules are well known. Everything stays editable.
PRESETS = (
    {
        "key": "ftmo_challenge",
        "label": "FTMO Challenge",
        "firm": "FTMO",
        "phase": "phase1",
        "profit_target_pct": 10.0,
        "max_daily_loss_pct": 5.0,
        "max_total_drawdown_pct": 10.0,
        "drawdown_mode": "static",
        "min_trading_days": 4,
        "max_calendar_days": None,
    },
    {
        "key": "ftmo_verification",
        "label": "FTMO Verification",
        "firm": "FTMO",
        "phase": "phase2",
        "profit_target_pct": 5.0,
        "max_daily_loss_pct": 5.0,
        "max_total_drawdown_pct": 10.0,
        "drawdown_mode": "static",
        "min_trading_days": 4,
        "max_calendar_days": None,
    },
    {
        "key": "ftmo_funded",
        "label": "FTMO Account (funded)",
        "firm": "FTMO",
        "phase": "funded",
        "profit_target_pct": None,
        "max_daily_loss_pct": 5.0,
        "max_total_drawdown_pct": 10.0,
        "drawdown_mode": "static",
        "min_trading_days": 0,
        "max_calendar_days": None,
    },
    {
        "key": "fundednext_stellar_2step_p1",
        "label": "FundedNext Stellar 2-Step - Phase 1",
        "firm": "FundedNext",
        "phase": "phase1",
        "profit_target_pct": 8.0,
        "max_daily_loss_pct": 5.0,
        "max_total_drawdown_pct": 10.0,
        "drawdown_mode": "static",
        "min_trading_days": 5,
        "max_calendar_days": None,
    },
    {
        "key": "fundednext_stellar_2step_p2",
        "label": "FundedNext Stellar 2-Step - Phase 2",
        "firm": "FundedNext",
        "phase": "phase2",
        "profit_target_pct": 5.0,
        "max_daily_loss_pct": 5.0,
        "max_total_drawdown_pct": 10.0,
        "drawdown_mode": "static",
        "min_trading_days": 5,
        "max_calendar_days": None,
    },
    {
        "key": "custom",
        "label": "Custom rules",
        "firm": "",
        "phase": "custom",
        "profit_target_pct": 10.0,
        "max_daily_loss_pct": 5.0,
        "max_total_drawdown_pct": 10.0,
        "drawdown_mode": "static",
        "min_trading_days": 0,
        "max_calendar_days": None,
    },
)
PRESET_KEYS = tuple(preset["key"] for preset in PRESETS)


def presets() -> list[dict]:
    return [{**preset, "note": VERIFY_NOTE} for preset in PRESETS]


# ------------------------------------------------------------------ ledger


def daily_realized(db: Session, since: datetime, before: datetime) -> dict[date, float]:
    """Net realised P&L per account-local day in ``[since, before)`` (same rules as ``realized_pnl``)."""

    def per_day(value, column, *conditions, join: bool = False):
        day = func.date(column)
        stmt = select(day, func.coalesce(func.sum(value), 0.0))
        if join:
            stmt = stmt.join(Trade, TradeEvent.trade_id == Trade.id)
        stmt = stmt.where(*conditions, column >= since, column < before).group_by(day)
        return db.execute(stmt).all()

    totals: dict[date, float] = {}

    def add(rows, sign: float = 1.0) -> None:
        for day, amount in rows:
            key = date.fromisoformat(day)
            totals[key] = totals.get(key, 0.0) + sign * float(amount or 0.0)

    add(per_day(Trade.pnl, CLOSE_TIME, Trade.status == "closed", Trade.managed.is_(False)))
    add(per_day(TradeEvent.pnl, TradeEvent.time, Trade.managed.is_(True), TradeEvent.lots > 0, join=True))
    add(per_day(Trade.fees, CLOSE_TIME, Trade.status == "closed", Trade.managed.is_(True)), sign=-1.0)
    return {day: round(amount, 2) for day, amount in totals.items()}


def trading_days(db: Session, start: date, until: date) -> int:
    since, before = datetime.combine(start, time.min), datetime.combine(until + timedelta(days=1), time.min)
    return int(
        db.scalar(
            select(func.count(func.distinct(func.date(Trade.entry_time)))).where(
                Trade.entry_time >= since, Trade.entry_time < before
            )
        )
        or 0
    )


# ------------------------------------------------------------------ metrics


@dataclass
class Evaluation:
    status: str  # what the rules say right now: active | passed | failed
    reason: str = ""
    ended_on: date | None = None


@dataclass
class Metrics:
    challenge_id: int
    today: date
    until: date  # last day covered (today, or the end date of an ended challenge)
    account_size: float
    balance: float
    profit: float
    profit_pct: float
    target_amount: float | None
    target_pct: float | None
    progress_pct: float | None
    daily_limit: float
    daily_loss_used: float
    open_risk: float
    daily_remaining: float
    drawdown_floor: float
    total_remaining: float
    trading_days: int
    min_trading_days: int
    days_elapsed: int
    days_left: int | None
    history: list[dict] = field(default_factory=list)
    evaluation: Evaluation = field(default_factory=lambda: Evaluation("active"))

    @property
    def target_reached(self) -> bool:
        return self.target_amount is not None and self.profit >= self.target_amount


def drawdown_floor(challenge: PropChallenge, peak_balance: float) -> float:
    allowance = challenge.account_size * challenge.max_total_drawdown_pct / 100.0
    if challenge.drawdown_mode == "trailing":
        return min(challenge.account_size, max(peak_balance, challenge.account_size) - allowance)
    return challenge.account_size - allowance


def compute(db: Session, settings: AccountSettings, challenge: PropChallenge, today: date | None = None) -> Metrics:
    """Metrics and daily history of ``challenge`` up to today (or its end date)."""
    today = today or local_today(settings)
    until = min(today, challenge.end_date) if challenge.end_date else today
    if challenge.max_calendar_days:  # nothing realised after the last allowed day counts
        until = min(until, challenge.start_date + timedelta(days=challenge.max_calendar_days - 1))
    until = max(until, challenge.start_date)
    since = datetime.combine(challenge.start_date, time.min)
    pnl_by_day = daily_realized(db, since, datetime.combine(until + timedelta(days=1), time.min))
    size = challenge.account_size
    daily_limit = size * challenge.max_daily_loss_pct / 100.0

    history: list[dict] = []
    balance, peak = size, size
    day = challenge.start_date
    while day <= until:
        floor = drawdown_floor(challenge, peak)  # the floor in force during the day
        pnl = pnl_by_day.get(day, 0.0)
        start_balance = balance
        balance = round(balance + pnl, 2)
        loss_used = max(0.0, -pnl)
        history.append(
            {
                "date": day,
                "pnl": pnl,
                "start_balance": start_balance,
                "balance": balance,
                "daily_loss_used": round(loss_used, 2),
                "drawdown_floor": round(floor, 2),
                "daily_breach": loss_used >= daily_limit > 0,
                "drawdown_breach": balance <= floor,
                "breached": loss_used >= daily_limit > 0 or balance <= floor,
            }
        )
        if day < today:  # only completed days move a trailing floor
            peak = max(peak, balance)
        day += timedelta(days=1)

    open_risk = compute_day_state(db, settings, today).open_risk if until == today else 0.0
    floor = drawdown_floor(challenge, peak)
    today_loss = history[-1]["daily_loss_used"] if history and history[-1]["date"] == today else 0.0
    profit = balance - size
    target_amount = size * challenge.profit_target_pct / 100.0 if challenge.profit_target_pct else None
    elapsed = (until - challenge.start_date).days + 1
    days = trading_days(db, challenge.start_date, until)
    metrics = Metrics(
        challenge_id=challenge.id,
        today=today,
        until=until,
        account_size=size,
        balance=round(balance, 2),
        profit=round(profit, 2),
        profit_pct=round(profit / size * 100.0, 2) if size else 0.0,
        target_amount=round(target_amount, 2) if target_amount is not None else None,
        target_pct=challenge.profit_target_pct,
        progress_pct=round(profit / target_amount * 100.0, 1) if target_amount else None,
        daily_limit=round(daily_limit, 2),
        daily_loss_used=round(today_loss, 2),
        open_risk=round(open_risk, 2),
        daily_remaining=round(daily_limit - today_loss - open_risk, 2),
        drawdown_floor=round(floor, 2),
        total_remaining=round(balance - floor - open_risk, 2),
        trading_days=days,
        min_trading_days=challenge.min_trading_days,
        days_elapsed=elapsed,
        days_left=max(0, challenge.max_calendar_days - elapsed) if challenge.max_calendar_days else None,
        history=history,
    )
    metrics.evaluation = evaluate(challenge, metrics)
    return metrics


def evaluate(challenge: PropChallenge, metrics: Metrics) -> Evaluation:
    """What the rules say about the challenge, from its history (first breach wins)."""
    for row in metrics.history:
        if row["daily_breach"]:
            return Evaluation("failed", f"Daily loss limit breached on {row['date']:%Y-%m-%d}", row["date"])
        if row["drawdown_breach"]:
            return Evaluation("failed", f"Max drawdown floor breached on {row['date']:%Y-%m-%d}", row["date"])
    if metrics.target_reached and metrics.trading_days >= challenge.min_trading_days:
        return Evaluation("passed", "Profit target reached with the minimum trading days", metrics.until)
    # Measured to today, not to ``until``: an ended challenge's metrics stop at its end date.
    if challenge.max_calendar_days and (metrics.today - challenge.start_date).days >= challenge.max_calendar_days:
        ended = challenge.start_date + timedelta(days=challenge.max_calendar_days - 1)
        return Evaluation("failed", f"Time limit of {challenge.max_calendar_days} days elapsed", ended)
    return Evaluation("active")


def refresh(db: Session, settings: AccountSettings, challenge: PropChallenge) -> Metrics:
    """Compute metrics and persist a pass/fail while the challenge is still running."""
    metrics = compute(db, settings, challenge)
    result = metrics.evaluation
    if challenge.status == "active" and result.status != "active":
        challenge.status = result.status
        challenge.end_date = result.ended_on or metrics.today
        db.commit()
        metrics = compute(db, settings, challenge)
    return metrics


def status_reason(challenge: PropChallenge, metrics: Metrics) -> str:
    if challenge.status == "active":
        return ""
    if challenge.status == "archived":
        return "Archived"
    if metrics.evaluation.status == challenge.status:
        return metrics.evaluation.reason
    return f"Marked as {challenge.status}"


def active_challenge(db: Session) -> PropChallenge | None:
    return db.scalar(select(PropChallenge).where(PropChallenge.active.is_(True)).order_by(PropChallenge.id.desc()))


def activate(db: Session, challenge: PropChallenge) -> None:
    """Make ``challenge`` the only one driving prop mode."""
    for other in db.scalars(select(PropChallenge).where(PropChallenge.active.is_(True))):
        other.active = False
    challenge.active = True
    db.commit()


def snapshot(challenge: PropChallenge, metrics: Metrics) -> dict:
    """The compact view shown in the status bar (``/api/risk/status`` -> ``prop``)."""
    return {
        "id": challenge.id,
        "name": challenge.name,
        "firm": challenge.firm,
        "phase": challenge.phase,
        "status": challenge.status,
        "enforce": challenge.enforce,
        "account_size": metrics.account_size,
        "balance": metrics.balance,
        "profit": metrics.profit,
        "profit_pct": metrics.profit_pct,
        "target_pct": metrics.target_pct,
        "progress_pct": metrics.progress_pct,
        "daily_limit": metrics.daily_limit,
        "daily_loss_used": metrics.daily_loss_used,
        "daily_remaining": metrics.daily_remaining,
        "drawdown_floor": metrics.drawdown_floor,
        "total_remaining": metrics.total_remaining,
        "trading_days": metrics.trading_days,
        "min_trading_days": metrics.min_trading_days,
        "days_left": metrics.days_left,
    }


def metrics_dict(metrics: Metrics) -> dict:
    data = {key: value for key, value in metrics.__dict__.items() if key not in ("history", "evaluation")}
    data["target_reached"] = metrics.target_reached
    return data
