"""Strategy Co-Pilot: checks a trade against every parameter of its strategy (pure).

Each unmet parameter becomes a ``Violation`` with a ``STRATEGY_*`` code whose
message names exactly what is missing and tells the trader to wait until it is
met. Severity follows ``settings.trade_rule_mode`` like the other per-trade
rules (the incubator/retired notices are always warnings). An empty parameter
list on the strategy means "any".
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .guardrails import EPSILON, Violation

COPILOT_CODES = frozenset(
    {
        "STRATEGY_REQUIRED_CONFLUENCES",
        "CONFLUENCES_BELOW_MIN",
        "STRATEGY_DIRECTION",
        "STRATEGY_SESSION",
        "STRATEGY_TIMEFRAME",
        "STRATEGY_BIAS",
        "STRATEGY_MIN_RR",
        "STRATEGY_INCUBATOR",
        "STRATEGY_RETIRED",
    }
)


@dataclass
class TradeContext:
    """The parts of a trade draft the Co-Pilot validates."""

    direction: str
    session: str = ""
    timeframe: str = ""
    confluences: list[str] = field(default_factory=list)
    planned_rr: float | None = None
    bias: str = ""  # today's pre-market bias: bullish | bearish | neutral | ""


def _join(items: list[str]) -> str:
    items = list(items)
    if len(items) <= 1:
        return "".join(items)
    return ", ".join(items[:-1]) + " and " + items[-1]


def strategy_violations(settings, strategy, ctx: TradeContext) -> list[Violation]:
    """Every strategy parameter the trade does not satisfy yet."""
    if strategy is None:
        return []
    severity = "block" if settings.trade_rule_mode == "block" else "warn"
    name = strategy.name
    violations: list[Violation] = []

    if strategy.status == "retired":
        violations.append(
            Violation("STRATEGY_RETIRED", f"Co-Pilot: {name} is retired - it is no longer part of your plan.", "warn")
        )
    elif strategy.status == "incubator":
        violations.append(
            Violation(
                "STRATEGY_INCUBATOR",
                f"Co-Pilot: {name} is still in the incubator. Prove its edge in backtests before trading it live, "
                "or trade it at reduced size.",
                "warn",
            )
        )

    required = list(strategy.required_confluences or [])
    present = set(ctx.confluences)
    missing = [name_ for name_ in required if name_ not in present]
    if missing:
        violations.append(
            Violation(
                "STRATEGY_REQUIRED_CONFLUENCES",
                f"Co-Pilot: {name} requires {_join(required)}. Missing: {_join(missing)}. "
                "Wait until every required confluence is present.",
                severity,
            )
        )

    allowed_direction = strategy.allowed_directions or "both"
    if allowed_direction != "both" and ctx.direction != allowed_direction:
        violations.append(
            Violation(
                "STRATEGY_DIRECTION",
                f"Co-Pilot: {name} only takes {allowed_direction} setups; this is a {ctx.direction} trade.",
                severity,
            )
        )

    sessions = list(strategy.allowed_sessions or [])
    if sessions and ctx.session not in sessions:
        where = f"in {ctx.session}" if ctx.session else "outside every session"
        violations.append(
            Violation(
                "STRATEGY_SESSION",
                f"Co-Pilot: {name} is only traded in {_join(sessions)}; this entry is {where}. "
                f"Wait for {_join(sessions)}.",
                severity,
            )
        )

    timeframes = list(strategy.allowed_timeframes or [])
    if timeframes:
        if not ctx.timeframe:
            message = f"Co-Pilot: select the setup timeframe - {name} is traded on {_join(timeframes)}."
        elif ctx.timeframe not in timeframes:
            message = f"Co-Pilot: {name} is traded on {_join(timeframes)}, not {ctx.timeframe}."
        else:
            message = ""
        if message:
            violations.append(Violation("STRATEGY_TIMEFRAME", message, severity))

    if strategy.require_bias_alignment:
        if ctx.bias in ("bullish", "bearish"):
            if (ctx.bias == "bullish") != (ctx.direction == "long"):
                violations.append(
                    Violation(
                        "STRATEGY_BIAS",
                        f"Co-Pilot: {name} trades with the daily bias, which is {ctx.bias} today; "
                        f"this is a {ctx.direction} trade. Wait for a setup in the direction of the bias.",
                        severity,
                    )
                )
        else:
            reason = "today's bias is neutral" if ctx.bias == "neutral" else "today's bias is not set"
            violations.append(
                Violation(
                    "STRATEGY_BIAS",
                    f"Co-Pilot: {name} only trades with the daily bias, and {reason}. "
                    "Set a directional bias in the pre-market routine first.",
                    severity,
                )
            )

    if strategy.min_rr and ctx.planned_rr is not None and ctx.planned_rr < strategy.min_rr - EPSILON:
        violations.append(
            Violation(
                "STRATEGY_MIN_RR",
                f"Co-Pilot: {name} needs at least 1:{strategy.min_rr:g}; this plan is 1:{ctx.planned_rr:.2f}. "
                "Wait for a better entry or a further target.",
                severity,
            )
        )

    return violations
