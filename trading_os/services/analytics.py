"""Performance statistics (pure functions over closed trades).

Definitions
-----------
* **Win rate** = winning trades / all closed trades (breakevens count in the
  denominator, which is the conservative convention).
* **Profit factor** = gross profit / |gross loss|; ``None`` when there are no
  losses (``profit_factor_infinite`` tells the UI to display infinity).
* **Expectancy** = net P&L / trades, i.e. the average money made per trade.
  Equivalent to ``win_rate * avg_win - loss_rate * avg_loss``.
* **R-multiple** = net P&L / initial risk (falls back to the price-based
  executed R:R when no risk amount was recorded).
* **Max drawdown** = largest peak-to-trough decline of the closed-trade
  equity curve, in money and in % of the peak.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable, Iterable, Sequence
from statistics import fmean
from typing import Protocol

WEEKDAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")
R_BIN_WIDTH = 0.5
R_BIN_MIN = -3.0
R_BIN_MAX = 5.0


class ClosedTrade(Protocol):
    id: int
    symbol: str
    direction: str
    strategy: str
    session: str
    emotion: str
    timeframe: str
    confluences: list[str]
    pnl: float
    fees: float
    r_multiple: float | None
    planned_rr: float | None
    executed_rr: float | None
    outcome: str
    rule_compliant: bool | None
    entry_time: object
    exit_time: object

    @property
    def close_time(self): ...


def _round(value: float | None, digits: int = 2) -> float | None:
    return None if value is None else round(value, digits)


def core_stats(trades: Sequence[ClosedTrade]) -> dict:
    """The headline metrics, also used for every breakdown row."""
    count = len(trades)
    wins = sum(1 for t in trades if t.outcome == "win")
    losses = sum(1 for t in trades if t.outcome == "loss")
    gross_profit = sum(t.pnl for t in trades if t.pnl > 0)
    gross_loss = sum(t.pnl for t in trades if t.pnl < 0)
    net = gross_profit + gross_loss
    r_values = [t.r_multiple for t in trades if t.r_multiple is not None]
    return {
        "trades": count,
        "wins": wins,
        "losses": losses,
        "breakeven": count - wins - losses,
        "win_rate": _round(wins / count * 100) if count else None,
        "net_pnl": _round(net),
        "gross_profit": _round(gross_profit),
        "gross_loss": _round(gross_loss),
        "profit_factor": _round(gross_profit / abs(gross_loss)) if gross_loss < 0 else None,
        "profit_factor_infinite": gross_loss == 0 and gross_profit > 0,
        "expectancy": _round(net / count) if count else None,
        "avg_r": _round(fmean(r_values)) if r_values else None,
    }


def _streaks(trades: Iterable[ClosedTrade]) -> tuple[int, int, int]:
    """Longest win streak, longest loss streak and the current streak (+wins/-losses)."""
    best_win = best_loss = current = 0
    for trade in trades:
        if trade.outcome == "win":
            current = current + 1 if current > 0 else 1
        elif trade.outcome == "loss":
            current = current - 1 if current < 0 else -1
        else:
            current = 0
        best_win = max(best_win, current)
        best_loss = max(best_loss, -current)
    return best_win, best_loss, current


def _drawdown(trades: Iterable[ClosedTrade], base_equity: float) -> tuple[float, float]:
    equity = peak = base_equity
    max_dd = max_dd_pct = 0.0
    for trade in trades:
        equity += trade.pnl
        peak = max(peak, equity)
        drawdown = peak - equity
        max_dd = max(max_dd, drawdown)
        if peak > 0:
            max_dd_pct = max(max_dd_pct, drawdown / peak * 100)
    return max_dd, max_dd_pct


def summarize(trades: Sequence[ClosedTrade], base_equity: float) -> dict:
    """Full metric set for the dashboard. ``trades`` must be in close order."""
    stats = core_stats(trades)
    win_pnls = [t.pnl for t in trades if t.outcome == "win"]
    loss_pnls = [t.pnl for t in trades if t.outcome == "loss"]
    avg_win = fmean(win_pnls) if win_pnls else None
    avg_loss = fmean(loss_pnls) if loss_pnls else None
    best_win, best_loss, current = _streaks(trades)
    max_dd, max_dd_pct = _drawdown(trades, base_equity)

    holds = [
        (t.exit_time - t.entry_time).total_seconds() / 60
        for t in trades
        if t.exit_time is not None and t.exit_time >= t.entry_time
    ]
    planned = [t.planned_rr for t in trades if t.planned_rr is not None]
    executed = [t.executed_rr for t in trades if t.executed_rr is not None]

    followed = [t for t in trades if t.rule_compliant is True]
    broken = [t for t in trades if t.rule_compliant is False]
    rated = len(followed) + len(broken)

    stats.update(
        {
            "avg_win": _round(avg_win),
            "avg_loss": _round(avg_loss),
            "payoff_ratio": _round(avg_win / abs(avg_loss)) if avg_win and avg_loss else None,
            "largest_win": _round(max(win_pnls)) if win_pnls else None,
            "largest_loss": _round(min(loss_pnls)) if loss_pnls else None,
            "max_consecutive_wins": best_win,
            "max_consecutive_losses": best_loss,
            "current_streak": current,
            "max_drawdown": _round(max_dd),
            "max_drawdown_pct": _round(max_dd_pct),
            "start_equity": _round(base_equity),
            "end_equity": _round(base_equity + sum(t.pnl for t in trades)),
            "return_pct": _round(sum(t.pnl for t in trades) / base_equity * 100) if base_equity > 0 else None,
            "total_fees": _round(sum(t.fees or 0.0 for t in trades)),
            "avg_hold_minutes": _round(fmean(holds), 1) if holds else None,
            "avg_planned_rr": _round(fmean(planned)) if planned else None,
            "avg_executed_rr": _round(fmean(executed)) if executed else None,
            "compliance_rate": _round(len(followed) / rated * 100) if rated else None,
            "followed_plan": core_stats(followed),
            "broke_rules": core_stats(broken),
        }
    )
    return stats


def equity_curve(trades: Sequence[ClosedTrade], base_equity: float) -> list[dict]:
    """One point per closed trade, preceded by the starting equity."""
    points = [{"n": 0, "t": None, "equity": _round(base_equity), "pnl": None, "symbol": None, "trade_id": None}]
    equity = base_equity
    for index, trade in enumerate(trades, start=1):
        equity += trade.pnl
        points.append(
            {
                "n": index,
                "t": trade.close_time.isoformat(),
                "equity": _round(equity),
                "pnl": _round(trade.pnl),
                "symbol": trade.symbol,
                "trade_id": trade.id,
            }
        )
    return points


def daily_pnl(trades: Sequence[ClosedTrade]) -> list[dict]:
    days: dict = defaultdict(lambda: {"pnl": 0.0, "trades": 0, "wins": 0, "losses": 0})
    for trade in trades:
        bucket = days[trade.close_time.date()]
        bucket["pnl"] += trade.pnl
        bucket["trades"] += 1
        bucket["wins"] += trade.outcome == "win"
        bucket["losses"] += trade.outcome == "loss"
    cumulative = 0.0
    out = []
    for day in sorted(days):
        bucket = days[day]
        cumulative += bucket["pnl"]
        out.append(
            {
                "date": day.isoformat(),
                "pnl": _round(bucket["pnl"]),
                "trades": bucket["trades"],
                "wins": bucket["wins"],
                "losses": bucket["losses"],
                "cumulative": _round(cumulative),
            }
        )
    return out


def r_distribution(trades: Sequence[ClosedTrade]) -> list[dict]:
    """Histogram of R-multiples in 0.5R bins centred on multiples of 0.5R."""
    bin_count = int(round((R_BIN_MAX - R_BIN_MIN) / R_BIN_WIDTH)) + 1
    bins = []
    for index in range(bin_count):
        center = R_BIN_MIN + index * R_BIN_WIDTH
        label = f"{center:+g}R" if center else "0R"
        if index == 0:
            label = f"≤{center:g}R"
        elif index == bin_count - 1:
            label = f"≥{center:+g}R"
        bins.append({"label": label, "center": center, "count": 0, "wins": 0, "losses": 0})

    for trade in trades:
        if trade.r_multiple is None:
            continue
        index = int(round((trade.r_multiple - R_BIN_MIN) / R_BIN_WIDTH))
        bucket = bins[min(max(index, 0), bin_count - 1)]
        bucket["count"] += 1
        bucket["wins"] += trade.outcome == "win"
        bucket["losses"] += trade.outcome == "loss"
    return bins


def _group(trades: Sequence[ClosedTrade], key: Callable[[ClosedTrade], str]) -> dict[str, list]:
    groups: dict[str, list] = defaultdict(list)
    for trade in trades:
        groups[key(trade)].append(trade)
    return groups


def _compliance_label(trade: ClosedTrade) -> str:
    if trade.rule_compliant is None:
        return "Not rated"
    return "Followed plan" if trade.rule_compliant else "Broke rules"


BREAKDOWN_KEYS: dict[str, Callable[[ClosedTrade], str]] = {
    "symbol": lambda t: t.symbol,
    "strategy": lambda t: t.strategy or "Unspecified",
    "session": lambda t: t.session or "Off-session",
    "emotion": lambda t: t.emotion or "Not logged",
    "direction": lambda t: t.direction.capitalize(),
    "weekday": lambda t: WEEKDAYS[t.entry_time.weekday()],
    "compliance": _compliance_label,
}


def _confluence_groups(trades: Sequence[ClosedTrade]) -> dict[str, list]:
    """A trade belongs to every confluence it lists, so groups overlap."""
    groups: dict[str, list] = defaultdict(list)
    for trade in trades:
        for name in trade.confluences or ["None recorded"]:
            groups[name].append(trade)
    return groups


def breakdowns(trades: Sequence[ClosedTrade]) -> dict[str, list[dict]]:
    out = {}
    grouped = {dimension: _group(trades, key) for dimension, key in BREAKDOWN_KEYS.items()}
    grouped["confluence"] = _confluence_groups(trades)
    for dimension, groups in grouped.items():
        rows = [{"key": name, **core_stats(group)} for name, group in groups.items()]
        if dimension == "weekday":
            rows.sort(key=lambda row: WEEKDAYS.index(row["key"]))
        else:
            rows.sort(key=lambda row: (row["net_pnl"] or 0.0), reverse=True)
        out[dimension] = rows
    return out


def full_report(trades: Sequence[ClosedTrade], base_equity: float) -> dict:
    return {
        "summary": summarize(trades, base_equity),
        "equity_curve": equity_curve(trades, base_equity),
        "daily_pnl": daily_pnl(trades),
        "distribution": r_distribution(trades),
        "breakdowns": breakdowns(trades),
    }
