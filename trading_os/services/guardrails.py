"""Hard risk rules (pure functions).

Rules are split in two families:

* **Trade rules** depend only on the trade itself (R:R, risk %, confluences,
  session). Their severity follows ``settings.trade_rule_mode``.
* **Daily rules** depend on the state of the trading day (checklist, daily
  loss, number of trades, remaining loss budget). Their severity follows
  ``settings.daily_limit_mode``; the checklist gate always blocks when on.

A ``block`` severity only stops *live* entries (a new open position dated
today or later). Trades logged after the fact are always recorded, with the
violations attached, so the journal stays truthful.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date

from ..constants import DAILY_LOSS_WARNING_RATIO

DAILY_CODES = frozenset({"CHECKLIST_INCOMPLETE", "DAILY_LOSS_LIMIT", "MAX_TRADES_REACHED", "DAILY_RISK_BUDGET"})
EPSILON = 1e-9


@dataclass(frozen=True)
class Violation:
    code: str
    message: str
    severity: str  # block | warn

    def to_dict(self) -> dict:
        return asdict(self)

    def as_warning(self) -> Violation:
        return Violation(self.code, self.message, "warn")


@dataclass
class DayState:
    """Snapshot of the current trading day used by the daily rules."""

    today: date
    start_of_day_equity: float
    current_equity: float
    daily_loss_limit: float
    realized_pnl_today: float
    open_risk: float
    open_trades: int
    trades_today: int
    checklist_complete: bool

    @property
    def loss_used(self) -> float:
        return max(0.0, -self.realized_pnl_today)

    @property
    def loss_used_ratio(self) -> float:
        return self.loss_used / self.daily_loss_limit if self.daily_loss_limit > 0 else 0.0

    @property
    def remaining_loss_budget(self) -> float:
        return max(0.0, self.daily_loss_limit - self.loss_used)


def _money(value: float, currency: str) -> str:
    return f"{value:,.2f} {currency}"


def trade_rule_violations(
    settings,
    *,
    planned_rr: float | None,
    has_take_profit: bool,
    risk_amount: float | None,
    equity: float,
    confluence_count: int,
    strategy_name: str = "",
    strategy_min_confluences: int = 0,
    in_trading_window: bool | None = None,
) -> list[Violation]:
    severity = "block" if settings.trade_rule_mode == "block" else "warn"
    violations: list[Violation] = []

    if settings.min_rr > 0:
        if not has_take_profit:
            violations.append(
                Violation(
                    "NO_TAKE_PROFIT",
                    f"No take profit set - R:R cannot be verified against your 1:{settings.min_rr:g} minimum.",
                    "warn",
                )
            )
        elif planned_rr is not None and planned_rr < settings.min_rr - EPSILON:
            violations.append(
                Violation(
                    "RR_BELOW_MIN",
                    f"Planned R:R 1:{planned_rr:.2f} is below your 1:{settings.min_rr:g} minimum.",
                    severity,
                )
            )

    if risk_amount and equity > 0:
        risk_pct = risk_amount / equity * 100
        if risk_pct > settings.max_risk_pct + 0.005:
            violations.append(
                Violation(
                    "RISK_ABOVE_MAX",
                    f"Risk of {risk_pct:.2f}% of equity exceeds your {settings.max_risk_pct:g}% max per trade.",
                    severity,
                )
            )

    if strategy_min_confluences and confluence_count < strategy_min_confluences:
        violations.append(
            Violation(
                "CONFLUENCES_BELOW_MIN",
                f"{strategy_name or 'This strategy'} requires {strategy_min_confluences} confluences; "
                f"{confluence_count} checked.",
                severity,
            )
        )

    if settings.warn_outside_sessions and in_trading_window is False:
        violations.append(Violation("OUTSIDE_SESSION", "Entry is outside your active trading sessions.", "warn"))

    return violations


def daily_violations(settings, day: DayState, new_risk: float | None = None) -> list[Violation]:
    severity = "block" if settings.daily_limit_mode == "lock" else "warn"
    currency = settings.currency
    violations: list[Violation] = []

    if settings.require_checklist and not day.checklist_complete:
        violations.append(
            Violation(
                "CHECKLIST_INCOMPLETE",
                "Complete today's pre-market checklist before entering trades.",
                "block",
            )
        )

    limit_hit = day.daily_loss_limit > 0 and day.loss_used >= day.daily_loss_limit - 0.005
    if limit_hit:
        violations.append(
            Violation(
                "DAILY_LOSS_LIMIT",
                f"Daily loss limit reached: -{_money(day.loss_used, currency)} of "
                f"{_money(day.daily_loss_limit, currency)} ({settings.max_daily_loss_pct:g}% max). "
                "Stop trading for today.",
                severity,
            )
        )

    if settings.max_trades_per_day and day.trades_today >= settings.max_trades_per_day:
        violations.append(
            Violation(
                "MAX_TRADES_REACHED",
                f"Max trades per day reached ({day.trades_today}/{settings.max_trades_per_day}).",
                severity,
            )
        )

    if new_risk and not limit_hit:
        budget = day.remaining_loss_budget - day.open_risk
        if new_risk > budget + 0.005:
            violations.append(
                Violation(
                    "DAILY_RISK_BUDGET",
                    f"This trade risks {_money(new_risk, currency)} but only "
                    f"{_money(max(budget, 0.0), currency)} of today's loss budget remains "
                    "(after open risk). A stop-out would breach the daily limit.",
                    severity,
                )
            )

    return violations


def daily_state(settings, day: DayState) -> str:
    """Traffic-light state of the day: ok | warning | locked | breached."""
    limit_hit = day.daily_loss_limit > 0 and day.loss_used >= day.daily_loss_limit - 0.005
    trades_hit = bool(settings.max_trades_per_day) and day.trades_today >= settings.max_trades_per_day
    if limit_hit or trades_hit:
        return "locked" if settings.daily_limit_mode == "lock" else "breached"
    if day.loss_used_ratio >= DAILY_LOSS_WARNING_RATIO:
        return "warning"
    return "ok"


def is_blocking(violations: list[Violation]) -> bool:
    return any(violation.severity == "block" for violation in violations)
