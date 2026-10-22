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
* **Max drawdown** = largest peak-to-trough decline of the realised equity
  curve (one point per booking, see below), in money and in % of the peak.
* **Mistake cost** = net P&L of the trades tagged with a mistake. A trade
  tagged with several mistakes counts towards each of them.

Bookings: when the money was realised
-------------------------------------
Outputs come in two families, following the position ledger of
``services.account.realized_pnl``:

* **Period P&L** (daily P&L, equity curve, drawdown, end equity, return) is
  built from *bookings*. A managed trade books each fill at its fill time, so
  partial exits land on the day they were filled, even while the trade is
  still open; its closing fill also carries the fees. Any other closed trade
  books its whole net P&L at its close time.
* **Trade statistics** (count, win rate, profit factor, expectancy, R,
  streaks, breakdowns) count each closed trade once, with its full net P&L.

Backtest rows and other adapters without fills book at their close, so every
function here keeps working for them unchanged.
"""

from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime
from statistics import fmean
from typing import Any, Protocol

WEEKDAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")
R_BIN_WIDTH = 0.5
R_BIN_MIN = -3.0
R_BIN_MAX = 5.0
NO_MISTAKES = "No mistakes"


class ClosedTrade(Protocol):
    id: int
    symbol: str
    direction: str
    strategy: str
    session: str
    emotion: str
    timeframe: str
    confluences: list[str]
    mistakes: list[str]
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


# ------------------------------------------------------------------ bookings


@dataclass(frozen=True)
class Booking:
    """Realised P&L booked at one moment: a fill of a managed trade, or a whole trade at its close."""

    time: datetime
    amount: float  # net of the fees on the closing booking, gross on earlier fills
    trade: Any
    kind: str  # fill kind (tp | partial | stop | close); "close" for a trade without fills
    closes: bool  # True on the booking that closes the trade (it carries the trade's outcome)
    lots: float | None = None
    price: float | None = None
    target_level: int | None = None
    event_id: int | None = None


def _fills(trade) -> list:
    """Fill events (lots > 0) of ``trade`` in ledger order: time, then id."""
    fills = [event for event in getattr(trade, "events", None) or () if (event.lots or 0.0) > 0]
    return sorted(fills, key=lambda e: (e.time, e.id if getattr(e, "id", None) is not None else math.inf))


def trade_bookings(trade) -> list[Booking]:
    """When ``trade`` realised its P&L (same rules as ``services.account.realized_pnl``).

    A managed trade books every fill at its fill time. The fill that closes the
    trade books what is left of the trade's net P&L (fees included), so a
    trade's bookings always add up exactly to ``trade.pnl``; the fills of a
    trade still open book their gross P&L. Any other closed trade books its net
    P&L at its close time, and an open trade without fills books nothing.
    """
    closed = getattr(trade, "status", "closed") == "closed" and trade.pnl is not None
    fills = _fills(trade) if getattr(trade, "managed", False) else []
    if not fills:
        if not closed:
            return []
        return [
            Booking(
                trade.close_time,
                trade.pnl,
                trade,
                "close",
                True,
                lots=getattr(trade, "position_size", None),
                price=getattr(trade, "exit_price", None),
            )
        ]
    out = []
    booked = 0.0
    for index, event in enumerate(fills):
        closes = closed and index == len(fills) - 1
        amount = round(trade.pnl - booked, 2) if closes else (event.pnl or 0.0)
        booked += amount
        out.append(
            Booking(
                event.time,
                amount,
                trade,
                event.kind,
                closes,
                lots=event.lots,
                price=event.price,
                target_level=getattr(event, "target_level", None),
                event_id=getattr(event, "id", None),
            )
        )
    return out


def bookings(trades: Iterable, *, since: datetime | None = None, before: datetime | None = None) -> list[Booking]:
    """Every booking of ``trades`` within ``[since, before)``, in time order (then trade id, then ledger order)."""
    out = [
        booking
        for trade in trades
        for booking in trade_bookings(trade)
        if (since is None or booking.time >= since) and (before is None or booking.time < before)
    ]
    out.sort(key=lambda b: (b.time, getattr(b.trade, "id", None) or 0))
    return out


def booking_stats(booked: Sequence[Booking]) -> dict:
    """P&L booked in a period, the trades it closed (with their outcomes) and its partial exits."""
    closed = [b.trade for b in booked if b.closes]
    wins = sum(1 for t in closed if t.outcome == "win")
    return {
        "pnl": round(sum(b.amount for b in booked), 2),
        "trades": len(closed),
        "wins": wins,
        "losses": sum(1 for t in closed if t.outcome == "loss"),
        "win_rate": round(wins / len(closed) * 100, 2) if closed else None,
        "partials": len(booked) - len(closed),
    }


def _mistakes(trade: ClosedTrade) -> list[str]:
    """Mistakes tagged on a trade; adapters without the attribute count as mistake-free."""
    return getattr(trade, "mistakes", None) or []


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


def _drawdown(amounts: Iterable[float], base_equity: float) -> tuple[float, float]:
    equity = peak = base_equity
    max_dd = max_dd_pct = 0.0
    for amount in amounts:
        equity += amount
        peak = max(peak, equity)
        drawdown = peak - equity
        max_dd = max(max_dd, drawdown)
        if peak > 0:
            max_dd_pct = max(max_dd_pct, drawdown / peak * 100)
    return max_dd, max_dd_pct


def summarize(trades: Sequence[ClosedTrade], base_equity: float, booked: Sequence[Booking] | None = None) -> dict:
    """Full metric set for the dashboard. ``trades`` must be in close order.

    With ``booked`` (see :func:`bookings`), the drawdown, end equity, return and
    ``realized_pnl`` follow the money realised in the period; without it each
    trade counts at its close (backtests).
    """
    stats = core_stats(trades)
    win_pnls = [t.pnl for t in trades if t.outcome == "win"]
    loss_pnls = [t.pnl for t in trades if t.outcome == "loss"]
    avg_win = fmean(win_pnls) if win_pnls else None
    avg_loss = fmean(loss_pnls) if loss_pnls else None
    best_win, best_loss, current = _streaks(trades)
    amounts = [b.amount for b in booked] if booked is not None else [t.pnl for t in trades]
    realized = sum(amounts)
    max_dd, max_dd_pct = _drawdown(amounts, base_equity)

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
    flawed = [t for t in trades if _mistakes(t)]

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
            "end_equity": _round(base_equity + realized),
            "return_pct": _round(realized / base_equity * 100) if base_equity > 0 else None,
            "realized_pnl": _round(realized),
            "total_fees": _round(sum(t.fees or 0.0 for t in trades)),
            "avg_hold_minutes": _round(fmean(holds), 1) if holds else None,
            "avg_planned_rr": _round(fmean(planned)) if planned else None,
            "avg_executed_rr": _round(fmean(executed)) if executed else None,
            "compliance_rate": _round(len(followed) / rated * 100) if rated else None,
            "followed_plan": core_stats(followed),
            "broke_rules": core_stats(broken),
            "mistake_free": core_stats([t for t in trades if not _mistakes(t)]),
            "with_mistakes": core_stats(flawed),
            "mistake_cost": mistake_cost(trades),
        }
    )
    return stats


def mistake_cost(trades: Sequence[ClosedTrade]) -> list[dict]:
    """Per tagged mistake: trades, net / average P&L and win rate, most expensive first."""
    rows = []
    for name, group in _overlapping_groups(trades, _mistakes, None).items():
        stats = core_stats(group)
        rows.append(
            {
                "mistake": name,
                "trades": stats["trades"],
                "net_pnl": stats["net_pnl"],
                "avg_pnl": stats["expectancy"],
                "win_rate": stats["win_rate"],
            }
        )
    rows.sort(key=lambda row: (row["net_pnl"], row["mistake"]))
    return rows


def equity_curve(trades: Sequence[ClosedTrade], base_equity: float) -> list[dict]:
    """One point per closed trade at its close, preceded by the starting equity (backtests)."""
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


def booking_equity_curve(booked: Sequence[Booking], base_equity: float) -> list[dict]:
    """The realised equity curve: one point per booking, preceded by the starting equity."""
    points = [
        {
            "n": 0,
            "t": None,
            "equity": _round(base_equity),
            "pnl": None,
            "symbol": None,
            "trade_id": None,
            "kind": None,
            "closes": None,
        }
    ]
    equity = base_equity
    for index, booking in enumerate(booked, start=1):
        equity += booking.amount
        points.append(
            {
                "n": index,
                "t": booking.time.isoformat(),
                "equity": _round(equity),
                "pnl": _round(booking.amount),
                "symbol": booking.trade.symbol,
                "trade_id": booking.trade.id,
                "kind": booking.kind,
                "closes": booking.closes,
            }
        )
    return points


def daily_pnl(trades: Sequence[ClosedTrade], booked: Sequence[Booking] | None = None) -> list[dict]:
    """P&L per day it was booked (default: the bookings of ``trades``).

    ``trades``/``wins``/``losses`` count the trades closed that day; ``partials``
    counts the fills that did not close their trade.
    """
    days: dict = defaultdict(lambda: {"pnl": 0.0, "trades": 0, "wins": 0, "losses": 0, "partials": 0})
    for booking in bookings(trades) if booked is None else booked:
        bucket = days[booking.time.date()]
        bucket["pnl"] += booking.amount
        if booking.closes:
            bucket["trades"] += 1
            bucket["wins"] += booking.trade.outcome == "win"
            bucket["losses"] += booking.trade.outcome == "loss"
        else:
            bucket["partials"] += 1
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
                "partials": bucket["partials"],
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
        # Half-way values go up (+0.25R -> +0.5R, -0.25R -> 0R) - round() would send them to the even bin.
        index = math.floor((trade.r_multiple - R_BIN_MIN) / R_BIN_WIDTH + 0.5)
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


def _overlapping_groups(
    trades: Sequence[ClosedTrade], names: Callable[[ClosedTrade], list[str]], fallback: str | None
) -> dict[str, list]:
    """A trade belongs to every name it lists (so groups overlap); unlabelled trades go to ``fallback``."""
    groups: dict[str, list] = defaultdict(list)
    for trade in trades:
        labels = names(trade) or ([fallback] if fallback else [])
        for name in labels:
            groups[name].append(trade)
    return groups


def breakdowns(trades: Sequence[ClosedTrade]) -> dict[str, list[dict]]:
    out = {}
    grouped = {dimension: _group(trades, key) for dimension, key in BREAKDOWN_KEYS.items()}
    grouped["confluence"] = _overlapping_groups(trades, lambda t: t.confluences, "None recorded")
    grouped["mistake"] = _overlapping_groups(trades, _mistakes, NO_MISTAKES)
    for dimension, groups in grouped.items():
        rows = [{"key": name, **core_stats(group)} for name, group in groups.items()]
        if dimension == "weekday":
            rows.sort(key=lambda row: WEEKDAYS.index(row["key"]))
        else:
            rows.sort(key=lambda row: (row["net_pnl"] or 0.0), reverse=True)
        out[dimension] = rows
    return out


def full_report(trades: Sequence[ClosedTrade], base_equity: float, booked: Sequence[Booking] | None = None) -> dict:
    """Trade statistics over ``trades`` (closed, in close order) and the period P&L - daily P&L, realised
    equity curve, drawdown - over ``booked``, by default the bookings of ``trades``."""
    if booked is None:
        booked = bookings(trades)
    return {
        "summary": summarize(trades, base_equity, booked),
        "equity_curve": booking_equity_curve(booked, base_equity),
        "daily_pnl": daily_pnl(trades, booked),
        "distribution": r_distribution(trades),
        "breakdowns": breakdowns(trades),
    }
