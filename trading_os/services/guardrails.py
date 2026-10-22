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
from .i18n import tr

# Context rules recorded at entry time and kept unchanged when a trade is edited.
DAILY_CODES = frozenset(
    {
        "CHECKLIST_INCOMPLETE",
        "DAILY_LOSS_LIMIT",
        "MAX_TRADES_REACHED",
        "DAILY_RISK_BUDGET",
        "NEWS_WINDOW",
        "KILL_SWITCH",
        "PROP_DAILY_LOSS",
        "PROP_MAX_DRAWDOWN",
        "PROP_RISK_BUDGET",
        "PROP_TARGET_REACHED",
        "PROP_CHALLENGE_ENDED",
    }
)
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
                    tr(
                        "No take profit set - R:R cannot be verified against your 1:{minimum:g} minimum.",
                        minimum=settings.min_rr,
                    ),
                    "warn",
                )
            )
        elif planned_rr is not None and planned_rr < settings.min_rr - EPSILON:
            violations.append(
                Violation(
                    "RR_BELOW_MIN",
                    tr(
                        "Planned R:R 1:{rr:.2f} is below your 1:{minimum:g} minimum.",
                        rr=planned_rr,
                        minimum=settings.min_rr,
                    ),
                    severity,
                )
            )

    if risk_amount and equity > 0:
        risk_pct = risk_amount / equity * 100
        if risk_pct > settings.max_risk_pct + 0.005:
            violations.append(
                Violation(
                    "RISK_ABOVE_MAX",
                    tr(
                        "Risk of {risk:.2f}% of equity exceeds your {cap:g}% max per trade.",
                        risk=risk_pct,
                        cap=settings.max_risk_pct,
                    ),
                    severity,
                )
            )

    if strategy_min_confluences and confluence_count < strategy_min_confluences:
        if strategy_name:
            message = tr(
                "{strategy} requires {required} confluences; {checked} checked.",
                strategy=strategy_name,
                required=strategy_min_confluences,
                checked=confluence_count,
            )
        else:
            message = tr(
                "This strategy requires {required} confluences; {checked} checked.",
                required=strategy_min_confluences,
                checked=confluence_count,
            )
        violations.append(Violation("CONFLUENCES_BELOW_MIN", message, severity))

    if settings.warn_outside_sessions and in_trading_window is False:
        severity = "block" if getattr(settings, "session_rule_mode", "warn") == "block" else "warn"
        violations.append(
            Violation("OUTSIDE_SESSION", tr("Entry is outside your active trading sessions."), severity)
        )

    return violations


def daily_violations(settings, day: DayState, new_risk: float | None = None) -> list[Violation]:
    severity = "block" if settings.daily_limit_mode == "lock" else "warn"
    currency = settings.currency
    violations: list[Violation] = []

    if settings.require_checklist and not day.checklist_complete:
        violations.append(
            Violation(
                "CHECKLIST_INCOMPLETE",
                tr("Complete today's pre-market checklist before entering trades."),
                "block",
            )
        )

    limit_hit = day.daily_loss_limit > 0 and day.loss_used >= day.daily_loss_limit - 0.005
    if limit_hit:
        violations.append(
            Violation(
                "DAILY_LOSS_LIMIT",
                tr(
                    "Daily loss limit reached: -{used} of {limit} ({cap:g}% max). Stop trading for today.",
                    used=_money(day.loss_used, currency),
                    limit=_money(day.daily_loss_limit, currency),
                    cap=settings.max_daily_loss_pct,
                ),
                severity,
            )
        )

    if settings.max_trades_per_day and day.trades_today >= settings.max_trades_per_day:
        violations.append(
            Violation(
                "MAX_TRADES_REACHED",
                tr(
                    "Max trades per day reached ({count}/{maximum}).",
                    count=day.trades_today,
                    maximum=settings.max_trades_per_day,
                ),
                severity,
            )
        )

    if new_risk and not limit_hit:
        budget = day.remaining_loss_budget - day.open_risk
        if new_risk > budget + 0.005:
            violations.append(
                Violation(
                    "DAILY_RISK_BUDGET",
                    tr(
                        "This trade risks {risk} but only {budget} of today's loss budget remains "
                        "(after open risk). A stop-out would breach the daily limit.",
                        risk=_money(new_risk, currency),
                        budget=_money(max(budget, 0.0), currency),
                    ),
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
