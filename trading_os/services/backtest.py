"""Strategy incubator: backtest trades, their metrics and the backtest-vs-live comparison.

Backtest trades live in their own table and never touch the live journal,
equity or daily limits. Results are kept in R-multiples (from entry/stop/exit,
or entered directly) and expressed in money with the strategy's
``backtest_risk_per_trade`` (default: the account's default risk % of the
starting balance), unless a money result was recorded for the trade.
Metrics reuse ``services.analytics`` so backtest and live numbers are computed
the same way.
"""

from __future__ import annotations

import csv
import io
import math
import re
from dataclasses import dataclass, field
from datetime import datetime
from statistics import fmean

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..models import AccountSettings, BacktestTrade, Strategy
from . import analytics
from . import trades as trade_service
from .i18n import N_, tr
from .sizing import planned_rr

MIN_BACKTEST_SAMPLE = 30
MIN_LIVE_SAMPLE = 10
# Expectancy gap (in R) beyond which live and backtest results are called different.
EXPECTANCY_TOLERANCE_R = 0.25


class BacktestError(ValueError):
    pass


# --------------------------------------------------------------------------- math


def r_from_prices(direction: str, entry: float, stop: float, exit_price: float) -> float:
    """Realised result in units of initial risk."""
    risk = abs(entry - stop)
    if risk == 0:
        raise BacktestError(tr("Stop loss must differ from the entry price"))
    sign = 1.0 if direction == "long" else -1.0
    return sign * (exit_price - entry) / risk


def outcome_for(value: float) -> str:
    rounded = round(value, 4)
    return "win" if rounded > 0 else "loss" if rounded < 0 else "breakeven"


def validate_geometry(direction: str, entry: float | None, stop: float | None, take_profit: float | None) -> None:
    long = direction == "long"
    if entry is not None and stop is not None:
        if long and stop >= entry:
            raise BacktestError(tr("For a long trade the stop loss must be below the entry price"))
        if not long and stop <= entry:
            raise BacktestError(tr("For a short trade the stop loss must be above the entry price"))
    if entry is not None and take_profit is not None:
        if long and take_profit <= entry:
            raise BacktestError(tr("For a long trade the take profit must be above the entry price"))
        if not long and take_profit >= entry:
            raise BacktestError(tr("For a short trade the take profit must be below the entry price"))


def derive(values: dict) -> dict:
    """Fill r_multiple / planned_rr / outcome from the given trade fields.

    Prices win over an entered R: with entry, stop and exit the R is computed.
    Without them an R (or at least a money result) is required.
    """
    direction = values["direction"]
    entry, stop, exit_price = values.get("entry_price"), values.get("stop_loss"), values.get("exit_price")
    validate_geometry(direction, entry, stop, values.get("take_profit"))
    if entry is not None and stop is not None and exit_price is not None:
        values["r_multiple"] = round(r_from_prices(direction, entry, stop, exit_price), 4)
    elif values.get("r_multiple") is None and values.get("pnl") is None:
        raise BacktestError(tr("Enter entry, stop and exit prices, or the result in R"))
    values["planned_rr"] = (
        round(planned_rr(entry, stop, values.get("take_profit")), 4)
        if entry is not None and stop is not None and values.get("take_profit") is not None
        else None
    )
    result = values.get("r_multiple") if values.get("r_multiple") is not None else values.get("pnl")
    values["outcome"] = outcome_for(result)
    return values


def risk_per_trade(strategy: Strategy, settings: AccountSettings) -> float:
    if strategy.backtest_risk_per_trade:
        return strategy.backtest_risk_per_trade
    return round(settings.starting_balance * settings.default_risk_pct / 100.0, 2)


@dataclass
class Row:
    """Adapter giving a backtest trade the shape ``services.analytics`` expects."""

    id: int
    symbol: str
    direction: str
    pnl: float
    r_multiple: float | None
    planned_rr: float | None
    outcome: str
    entry_time: datetime
    session: str = ""
    timeframe: str = ""
    strategy: str = ""
    emotion: str = ""
    fees: float = 0.0
    executed_rr: float | None = None
    rule_compliant: bool | None = None
    exit_time: datetime | None = None
    confluences: list[str] = field(default_factory=list)
    mistakes: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)

    @property
    def close_time(self) -> datetime:
        return self.entry_time


def to_row(trade: BacktestTrade, one_r: float, strategy_name: str = "") -> Row:
    if trade.pnl is not None:
        pnl = trade.pnl
    else:
        pnl = round((trade.r_multiple or 0.0) * one_r, 2)
    r_multiple = trade.r_multiple if trade.r_multiple is not None else (pnl / one_r if one_r else None)
    return Row(
        id=trade.id,
        symbol=trade.symbol,
        direction=trade.direction,
        pnl=pnl,
        r_multiple=r_multiple,
        planned_rr=trade.planned_rr,
        executed_rr=r_multiple,
        outcome=trade.outcome or outcome_for(pnl),
        entry_time=trade.trade_time,
        session=trade.session,
        timeframe=trade.timeframe,
        strategy=strategy_name,
        tags=list(trade.tags or []),
    )


def _max_drawdown_r(rows: list[Row]) -> float:
    total = peak = max_dd = 0.0
    for row in rows:
        total += row.r_multiple or 0.0
        peak = max(peak, total)
        max_dd = max(max_dd, peak - total)
    return round(max_dd, 2)


def metrics(rows: list[Row], base_equity: float) -> dict:
    """Backtest metrics: the journal's summary plus R-based figures."""
    summary = analytics.summarize(rows, base_equity)
    r_values = [row.r_multiple for row in rows if row.r_multiple is not None]
    win_r = [r for r in r_values if r > 0]
    loss_r = [r for r in r_values if r < 0]
    summary.update(
        {
            "net_r": round(sum(r_values), 2),
            "expectancy_r": round(fmean(r_values), 2) if r_values else None,
            "avg_win_r": round(fmean(win_r), 2) if win_r else None,
            "avg_loss_r": round(fmean(loss_r), 2) if loss_r else None,
            # Average realised reward:risk - average winner over average loser, in R.
            "avg_rr": round(fmean(win_r) / abs(fmean(loss_r)), 2) if win_r and loss_r else None,
            "max_drawdown_r": _max_drawdown_r(rows),
        }
    )
    return summary


def r_equity_curve(rows: list[Row], base_equity: float) -> list[dict]:
    points = analytics.equity_curve(rows, base_equity)
    total = 0.0
    points[0]["r"] = 0.0
    for point, row in zip(points[1:], rows, strict=True):
        total += row.r_multiple or 0.0
        point["r"] = round(total, 2)
    return points


# ------------------------------------------------------------------- comparison

COMPARE_KEYS = (
    ("trades", N_("Trades")),
    ("win_rate", N_("Win rate %")),
    ("profit_factor", N_("Profit factor")),
    ("expectancy_r", N_("Expectancy (R / trade)")),
    ("expectancy", N_("Expectancy ($ / trade)")),
    ("expectancy_same_basis", N_("Expectancy at backtest risk ($ / trade)")),
    ("avg_rr", N_("Avg win : avg loss (R)")),
    ("avg_planned_rr", N_("Avg planned R:R")),
    ("max_drawdown_r", N_("Max drawdown (R)")),
    ("max_drawdown", N_("Max drawdown ($)")),
    ("max_drawdown_pct", N_("Max drawdown %")),
)
# How each comparison value is shown: money, R, percent (of equity / of trades) or a plain number.
COMPARE_UNITS = {
    "expectancy": "money",
    "expectancy_same_basis": "money",
    "max_drawdown": "money",
    "expectancy_r": "r",
    "max_drawdown_r": "r",
    "win_rate": "pct",
    "max_drawdown_pct": "pct",
}


def expectancy_gap(backtest: dict, live: dict, one_r: float | None) -> dict | None:
    """How far live expectancy is from the backtest, in R and in money at the backtest's risk per trade.

    Both sides are compared in R, so position size and fees do not distort the gap; ``live_money_actual``
    is what live trades really made per trade (real size, net of fees).
    """
    b, l_ = backtest.get("expectancy_r"), live.get("expectancy_r")
    if not backtest.get("trades") or not live.get("trades") or b is None or l_ is None:
        return None
    gap = l_ - b
    return {
        "backtest_r": b,
        "live_r": l_,
        "gap_r": round(gap, 2),
        "gap_pct": round(gap / abs(b) * 100, 1) if b else None,
        "edge_retained_pct": round(l_ / b * 100, 1) if b > 0 else None,
        "backtest_money": round(b * one_r, 2) if one_r else None,
        "live_money_same_basis": round(l_ * one_r, 2) if one_r else None,
        "gap_money_same_basis": round(gap * one_r, 2) if one_r else None,
        "live_money_actual": live.get("expectancy"),
    }


def compare(backtest: dict, live: dict, one_r: float | None = None) -> dict:
    """Side-by-side rows (live - backtest), the expectancy gap and a verdict.

    ``one_r`` (money per 1R in backtests) adds the live expectancy at the backtest's risk basis.
    """
    sides = {"backtest": dict(backtest), "live": dict(live)}
    for side in sides.values():
        side["expectancy_same_basis"] = (
            round(side["expectancy_r"] * one_r, 2) if one_r and side.get("expectancy_r") is not None else None
        )
    rows = []
    for key, label in COMPARE_KEYS:
        if key == "expectancy_same_basis" and not one_r:
            continue
        b, l_ = sides["backtest"].get(key), sides["live"].get(key)
        # An empty side has no statistics (only its trade count of 0).
        if key != "trades":
            b = b if backtest["trades"] else None
            l_ = l_ if live["trades"] else None
        delta = round(l_ - b, 2) if isinstance(b, (int, float)) and isinstance(l_, (int, float)) else None
        row = {"key": key, "label": tr(label), "unit": COMPARE_UNITS.get(key, "number"), "backtest": b, "live": l_}
        row["delta"] = delta
        if key == "profit_factor":  # no losing trade: infinite, shown as such rather than as "no value"
            row["infinite"] = {
                "backtest": bool(backtest["trades"] and backtest.get("profit_factor_infinite")),
                "live": bool(live["trades"] and live.get("profit_factor_infinite")),
            }
        rows.append(row)
    return {"rows": rows, "gap": expectancy_gap(backtest, live, one_r), "verdict": verdict(backtest, live)}


def verdict(backtest: dict, live: dict) -> dict:
    """A plain-language read of how live trading tracks the backtest."""
    notes = []
    if backtest["trades"] < MIN_BACKTEST_SAMPLE:
        notes.append(
            tr(
                "Backtest sample is small ({count} trades) - aim for at least {minimum} before trusting the edge.",
                count=backtest["trades"],
                minimum=MIN_BACKTEST_SAMPLE,
            )
        )
    if live["trades"] < MIN_LIVE_SAMPLE:
        return {
            "status": "insufficient",
            "message": tr(
                "Not enough live trades to compare yet ({count} of {minimum}).",
                count=live["trades"],
                minimum=MIN_LIVE_SAMPLE,
            ),
            "notes": notes,
        }
    b, l_ = backtest.get("expectancy_r"), live.get("expectancy_r")
    if b is None or l_ is None:
        return {
            "status": "insufficient",
            "message": tr("Expectancy in R is not available for both sets."),
            "notes": notes,
        }
    if l_ < b - EXPECTANCY_TOLERANCE_R or (b > 0 >= l_):
        status, message = "below", tr(
            "Live results lag the backtest ({live_r:+.2f}R vs {backtest_r:+.2f}R per trade) - review execution, "
            "rule compliance and whether market conditions changed.",
            live_r=l_,
            backtest_r=b,
        )
    elif l_ > b + EXPECTANCY_TOLERANCE_R:
        status, message = "above", tr(
            "Live results beat the backtest ({live_r:+.2f}R vs {backtest_r:+.2f}R per trade) - small samples "
            "overstate edges, keep logging.",
            live_r=l_,
            backtest_r=b,
        )
    else:
        status, message = "in_line", tr(
            "Live results are in line with the backtest ({live_r:+.2f}R vs {backtest_r:+.2f}R per trade).",
            live_r=l_,
            backtest_r=b,
        )
    return {"status": status, "message": message, "notes": notes}


# ------------------------------------------------------------------- database


def list_trades(db: Session, strategy: Strategy) -> list[BacktestTrade]:
    stmt = select(BacktestTrade).where(BacktestTrade.strategy_id == strategy.id)
    return list(db.scalars(stmt.order_by(BacktestTrade.trade_time, BacktestTrade.id)))


def trade_counts(db: Session) -> dict[int, int]:
    stmt = select(BacktestTrade.strategy_id, func.count(BacktestTrade.id)).group_by(BacktestTrade.strategy_id)
    return dict(db.execute(stmt).all())


def strategy_report(db: Session, settings: AccountSettings, strategy: Strategy) -> dict:
    """Metrics, curves, distribution and the live comparison for one strategy."""
    one_r = risk_per_trade(strategy, settings)
    rows = [to_row(trade, one_r, strategy.name) for trade in list_trades(db, strategy)]
    base = settings.starting_balance
    backtest_metrics = metrics(rows, base)

    names = live_names(db, strategy)
    live_trades = trade_service.closed_trades(db, trade_service.TradeFilters(strategy_names=names))
    live_metrics = metrics(live_trades, base) if live_trades else metrics([], base)
    return {
        "strategy": {"id": strategy.id, "name": strategy.name, "status": strategy.status},
        "risk_per_trade": one_r,
        "currency": settings.currency,
        "metrics": backtest_metrics,
        "equity_curve": r_equity_curve(rows, base),
        "distribution": analytics.r_distribution(rows),
        "live_metrics": live_metrics,
        "live_names": list(names),
        "comparison": compare(backtest_metrics, live_metrics, one_r),
    }


def live_names(db: Session, strategy: Strategy) -> tuple[str, ...]:
    """Journal strategy names that count as this strategy: its name and its former names, except names
    another strategy uses now (those trades belong to that strategy)."""
    taken = set(db.scalars(select(Strategy.name).where(Strategy.id != strategy.id)))
    former = [name for name in (strategy.former_names or []) if name and name != strategy.name and name not in taken]
    return (strategy.name, *dict.fromkeys(former))


def strategy_renamed(db: Session, strategy: Strategy, before: dict) -> None:
    """Remember a strategy's old name so its journal history keeps counting (trades are never rewritten)."""
    old = before.get("name")
    if not old or old == strategy.name:
        return
    names = [name for name in (strategy.former_names or []) if name not in (old, strategy.name)]
    strategy.former_names = [*names, old]


# ------------------------------------------------------------------ CSV import

HEADER_ALIASES = {
    "trade_time": ("date", "time", "datetime", "trade_time", "open_time", "entry_time", "opened", "timestamp"),
    "symbol": ("symbol", "asset", "pair", "instrument", "market", "ticker"),
    "direction": ("direction", "side", "type", "action", "position"),
    "entry_price": ("entry", "entry_price", "open", "open_price", "price_in", "entry_level"),
    "stop_loss": ("stop", "sl", "stop_loss", "stoploss", "s_l"),
    "take_profit": ("tp", "take_profit", "takeprofit", "target", "t_p"),
    "exit_price": ("exit", "exit_price", "close", "close_price", "price_out"),
    "r_multiple": ("r", "r_multiple", "rmultiple", "result_r", "r_result", "rr_result", "r_r"),
    "pnl": ("pnl", "p&l", "p_l", "profit", "net", "net_pnl", "result"),
    "session": ("session",),
    "timeframe": ("timeframe", "tf"),
    "notes": ("notes", "note", "comment", "comments"),
}
DIRECTION_ALIASES = {"long": "long", "buy": "long", "b": "long", "short": "short", "sell": "short", "s": "short"}
DATE_FORMATS = (
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%d %H:%M",
    "%Y-%m-%d",
    "%Y.%m.%d %H:%M:%S",
    "%Y.%m.%d %H:%M",
    "%Y.%m.%d",
    "%Y/%m/%d %H:%M",
    "%Y/%m/%d",
    "%d/%m/%Y %H:%M:%S",
    "%d/%m/%Y %H:%M",
    "%d/%m/%Y",
    "%d.%m.%Y %H:%M",
    "%d.%m.%Y",
)


def _normalise_header(name: str) -> str:
    name = name.strip().lower()
    return re.sub(r"[^a-z0-9&]+", "_", name).strip("_")


PRICE_FIELDS = ("entry_price", "stop_loss", "take_profit", "exit_price")
PRICE_FIELD_NAMES = {
    "entry_price": N_("entry price"),
    "stop_loss": N_("stop loss"),
    "take_profit": N_("take profit"),
    "exit_price": N_("exit price"),
}


def _parse_number(raw: str, decimal_comma: bool = False) -> float | None:
    """A number as written in a spreadsheet export.

    With both separators the last one is the decimal mark ("1.234,56", "1,234.56"). A lone comma is a
    decimal comma in semicolon/tab files; in comma files ("1,0850" vs "18,550") three trailing digits
    after a single comma mean thousands.
    """
    text = raw.strip().replace("−", "-").replace(" ", "").replace("\u00a0", "")
    if not text:
        return None
    text = text.removesuffix("R").removesuffix("r").replace("$", "").replace("€", "")
    if "," in text and "." in text:
        decimal, thousands = (",", ".") if text.rfind(",") > text.rfind(".") else (".", ",")
        text = text.replace(thousands, "").replace(decimal, ".")
    elif "," in text:
        head, _, tail = text.rpartition(",")
        thousands = not decimal_comma and len(tail) == 3 and head.lstrip("-").isdigit() and "," not in head
        text = head.replace(",", "") + ("" if thousands else ".") + tail
    try:
        value = float(text)
    except ValueError as exc:
        raise BacktestError(tr("'{value}' is not a number", value=raw.strip())) from exc
    if not math.isfinite(value):
        raise BacktestError(tr("'{value}' is not a finite number", value=raw.strip()))
    return value


def _parse_date(raw: str) -> datetime:
    text = raw.strip().replace("T", " ")
    try:
        return datetime.fromisoformat(text).replace(tzinfo=None, microsecond=0)
    except ValueError:
        pass
    for pattern in DATE_FORMATS:
        try:
            return datetime.strptime(text, pattern)
        except ValueError:
            continue
    raise BacktestError(tr("Unrecognised date '{value}' (use YYYY-MM-DD HH:MM or DD/MM/YYYY)", value=raw.strip()))


def parse_csv(text: str) -> tuple[list[dict], list[dict]]:
    """Parse pasted CSV (comma, semicolon or tab). Returns (rows, errors[{line, message}])."""
    text = text.strip("﻿\n\r ")
    if not text:
        raise BacktestError(tr("The CSV is empty"))
    first_line = text.splitlines()[0]
    delimiter = max((",", ";", "\t"), key=first_line.count)
    decimal_comma = delimiter != ","
    reader = csv.reader(io.StringIO(text), delimiter=delimiter)
    header = next(reader)
    columns: dict[int, str] = {}
    for index, name in enumerate(header):
        key = _normalise_header(name)
        for field_name, aliases in HEADER_ALIASES.items():
            if key in aliases and field_name not in columns.values():
                columns[index] = field_name
                break
    if "direction" not in columns.values():
        raise BacktestError(tr("The CSV needs a direction column (direction / side / type: buy, sell, long, short)"))
    if "trade_time" not in columns.values():
        raise BacktestError(tr("The CSV needs a date column (date / time / datetime)"))

    rows, errors = [], []
    for line_number, record in enumerate(reader, start=2):
        if not any(cell.strip() for cell in record):
            continue
        try:
            raw = {columns[i]: cell for i, cell in enumerate(record) if i in columns}
            values: dict = {
                "trade_time": _parse_date(raw.get("trade_time", "")),
                "symbol": raw.get("symbol", "").strip().upper()[:20],
                "session": raw.get("session", "").strip()[:40],
                "timeframe": raw.get("timeframe", "").strip().upper()[:10],
                "notes": raw.get("notes", "").strip()[:5000],
            }
            direction = DIRECTION_ALIASES.get(raw.get("direction", "").strip().lower())
            if direction is None:
                raise BacktestError(tr("Unknown direction '{value}'", value=raw.get("direction", "").strip()))
            values["direction"] = direction
            for key in ("entry_price", "stop_loss", "take_profit", "exit_price", "r_multiple", "pnl"):
                values[key] = _parse_number(raw[key], decimal_comma) if key in raw else None
            for key in PRICE_FIELDS:
                if values[key] is not None and values[key] < 0:
                    raise BacktestError(tr("{field} cannot be negative", field=tr(PRICE_FIELD_NAMES[key])))
                if values[key] == 0:  # platforms write 0 for "no stop / no target / not closed"
                    values[key] = None
            rows.append(derive(values))
        except BacktestError as exc:
            errors.append({"line": line_number, "message": str(exc)})
    return rows, errors


def _csv_text(value: str) -> str:
    """Neutralise spreadsheet formula injection in a text cell."""
    return "'" + value if value[:1] in ("=", "+", "-", "@", "\t", "\r") else value


def export_csv(trades: list[BacktestTrade]) -> str:
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(
        [
            "date",
            "symbol",
            "direction",
            "entry",
            "stop",
            "take_profit",
            "exit",
            "r",
            "pnl",
            "session",
            "timeframe",
            "notes",
        ]
    )
    for t in trades:
        notes = _csv_text(t.notes or "")
        writer.writerow(
            [
                t.trade_time.isoformat(sep=" ", timespec="minutes"),
                _csv_text(t.symbol or ""),
                t.direction,
                *("" if v is None else v for v in (t.entry_price, t.stop_loss, t.take_profit, t.exit_price)),
                "" if t.r_multiple is None else t.r_multiple,
                "" if t.pnl is None else t.pnl,
                _csv_text(t.session or ""),
                _csv_text(t.timeframe or ""),
                notes,
            ]
        )
    return buffer.getvalue()
