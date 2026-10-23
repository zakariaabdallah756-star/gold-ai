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
from .i18n import N_, tr

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
BIAS_LABELS = {"bullish": N_("bullish"), "bearish": N_("bearish")}


@dataclass
class TradeContext:
    """The parts of a trade draft the Co-Pilot validates."""

    direction: str
    session: str = ""  # the session the trade is filed under
    timeframe: str = ""
    confluences: list[str] = field(default_factory=list)
    planned_rr: float | None = None
    bias: str = ""  # today's pre-market bias: bullish | bearish | neutral | ""
    open_sessions: list[str] = field(default_factory=list)  # every session open at entry (overlaps)


def _join(items: list[str]) -> str:
    items = list(items)
    if len(items) <= 1:
        return "".join(items)
    return tr("{items} and {last}", items=", ".join(items[:-1]), last=items[-1])


def strategy_violations(settings, strategy, ctx: TradeContext) -> list[Violation]:
    """Every strategy parameter the trade does not satisfy yet."""
    if strategy is None:
        return []
    severity = "block" if settings.trade_rule_mode == "block" else "warn"
    name = strategy.name
    violations: list[Violation] = []

    if strategy.status == "retired":
        violations.append(
            Violation(
                "STRATEGY_RETIRED",
                tr("Co-Pilot: {strategy} is retired - it is no longer part of your plan.", strategy=name),
                "warn",
            )
        )
    elif strategy.status == "incubator":
        violations.append(
            Violation(
                "STRATEGY_INCUBATOR",
                tr(
                    "Co-Pilot: {strategy} is still in the incubator. Prove its edge in backtests before trading it "
                    "live, or trade it at reduced size.",
                    strategy=name,
                ),
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
                tr(
                    "Co-Pilot: {strategy} requires {required}. Missing: {missing}. "
                    "Wait until every required confluence is present.",
                    strategy=name,
                    required=_join(required),
                    missing=_join(missing),
                ),
                severity,
            )
        )

    allowed_direction = strategy.allowed_directions or "both"
    if allowed_direction != "both" and ctx.direction != allowed_direction:
        violations.append(
            Violation(
                "STRATEGY_DIRECTION",
                tr(
                    "Co-Pilot: {strategy} only takes {allowed} setups; this is a {direction} trade.",
                    strategy=name,
                    allowed=allowed_direction,
                    direction=ctx.direction,
                ),
                severity,
            )
        )

    sessions = list(strategy.allowed_sessions or [])
    if sessions and ctx.session not in sessions and not set(ctx.open_sessions) & set(sessions):
        if ctx.session:
            message = tr(
                "Co-Pilot: {strategy} is only traded in {sessions}; this entry is in {session}. Wait for {sessions}.",
                strategy=name,
                sessions=_join(sessions),
                session=ctx.session,
            )
        else:
            message = tr(
                "Co-Pilot: {strategy} is only traded in {sessions}; this entry is outside every session. "
                "Wait for {sessions}.",
                strategy=name,
                sessions=_join(sessions),
            )
        violations.append(Violation("STRATEGY_SESSION", message, severity))

    timeframes = list(strategy.allowed_timeframes or [])
    if timeframes:
        if not ctx.timeframe:
            message = tr(
                "Co-Pilot: select the setup timeframe - {strategy} is traded on {timeframes}.",
                strategy=name,
                timeframes=_join(timeframes),
            )
        elif ctx.timeframe not in timeframes:
            message = tr(
                "Co-Pilot: {strategy} is traded on {timeframes}, not {timeframe}.",
                strategy=name,
                timeframes=_join(timeframes),
                timeframe=ctx.timeframe,
            )
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
                        tr(
                            "Co-Pilot: {strategy} trades with the daily bias, which is {bias} today; "
                            "this is a {direction} trade. Wait for a setup in the direction of the bias.",
                            strategy=name,
                            bias=tr(BIAS_LABELS[ctx.bias]),
                            direction=ctx.direction,
                        ),
                        severity,
                    )
                )
        else:
            if ctx.bias == "neutral":
                message = tr(
                    "Co-Pilot: {strategy} only trades with the daily bias, and today's bias is neutral. "
                    "Set a directional bias in the pre-market routine first.",
                    strategy=name,
                )
            else:
                message = tr(
                    "Co-Pilot: {strategy} only trades with the daily bias, and today's bias is not set. "
                    "Set a directional bias in the pre-market routine first.",
                    strategy=name,
                )
            violations.append(Violation("STRATEGY_BIAS", message, severity))

    if strategy.min_rr and ctx.planned_rr is not None and ctx.planned_rr < strategy.min_rr - EPSILON:
        violations.append(
            Violation(
                "STRATEGY_MIN_RR",
                tr(
                    "Co-Pilot: {strategy} needs at least 1:{minimum:g}; this plan is 1:{rr:.2f}. "
                    "Wait for a better entry or a further target.",
                    strategy=name,
                    minimum=strategy.min_rr,
                    rr=ctx.planned_rr,
                ),
                severity,
            )
        )

    return violations
