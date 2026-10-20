"""Setup detectors for the strategy alert engine (pure functions).

Indicators (``ema``, ``rsi``, ``atr``, ``swings``) work on plain lists and
return one value per bar (``None`` while there is not enough history yet).
Condition types are registered in ``CONDITION_TYPES``; each evaluates the
closed bars of one symbol - the *last* bar is the signal candle - and returns a
``Result``: whether it is met, the direction it points to (``None`` for a
neutral filter), a plain-language explanation with the key values and, when
the setup has a natural invalidation level, a ``stop_hint`` for the stop loss.

Bars are any objects with ``open``, ``high``, ``low`` and ``close`` attributes
(see ``services.market_data.Bar``).
"""

from __future__ import annotations

import math
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol


class BarLike(Protocol):
    open: float
    high: float
    low: float
    close: float


@dataclass
class Result:
    met: bool
    direction: str | None  # long | short | None (neutral filter)
    label: str  # explanation with the key values
    stop_hint: float | None = None  # structure level for the stop loss
    details: dict = field(default_factory=dict)


@dataclass(frozen=True)
class ParamSpec:
    name: str
    label: str
    kind: str  # int | float | choice | levels
    default: Any
    min: float | None = None
    max: float | None = None
    choices: tuple[str, ...] = ()
    help: str = ""

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "label": self.label,
            "kind": self.kind,
            "default": list(self.default) if isinstance(self.default, (list, tuple)) else self.default,
            "min": self.min,
            "max": self.max,
            "choices": list(self.choices),
            "help": self.help,
        }


@dataclass(frozen=True)
class ConditionType:
    key: str
    label: str
    description: str
    directional: bool  # False = neutral filter that never sets the direction on its own
    confluence: str  # default confluence label (matches the default plan confluences where natural)
    params: tuple[ParamSpec, ...]
    fn: Callable[[Sequence[BarLike], dict, dict], Result]
    min_bars: Callable[[dict], int]

    def to_dict(self) -> dict:
        return {
            "key": self.key,
            "label": self.label,
            "description": self.description,
            "directional": self.directional,
            "confluence": self.confluence,
            "params": [spec.to_dict() for spec in self.params],
        }


# --------------------------------------------------------------------------- formatting


def fmt_price(value: float) -> str:
    """Compact price for explanations: 5 decimals below 10, 3 below 1000, else 2."""
    digits = 5 if abs(value) < 10 else 3 if abs(value) < 1000 else 2
    return f"{value:.{digits}f}"


# --------------------------------------------------------------------------- indicators


def ema(values: Sequence[float], period: int) -> list[float | None]:
    """Exponential moving average seeded with the simple average of the first ``period`` values."""
    out: list[float | None] = [None] * len(values)
    if period < 1 or len(values) < period:
        return out
    alpha = 2.0 / (period + 1)
    current = sum(values[:period]) / period
    out[period - 1] = current
    for index in range(period, len(values)):
        current = values[index] * alpha + current * (1 - alpha)
        out[index] = current
    return out


def rsi(closes: Sequence[float], period: int = 14) -> list[float | None]:
    """Wilder's RSI: averages seeded with the simple mean of the first ``period`` changes."""
    out: list[float | None] = [None] * len(closes)
    if period < 1 or len(closes) <= period:
        return out
    changes = [closes[i] - closes[i - 1] for i in range(1, len(closes))]
    avg_gain = sum(max(change, 0.0) for change in changes[:period]) / period
    avg_loss = sum(max(-change, 0.0) for change in changes[:period]) / period

    def value(gain: float, loss: float) -> float:
        if loss == 0:
            return 50.0 if gain == 0 else 100.0
        return 100.0 - 100.0 / (1.0 + gain / loss)

    out[period] = value(avg_gain, avg_loss)
    for index in range(period, len(changes)):
        change = changes[index]
        avg_gain = (avg_gain * (period - 1) + max(change, 0.0)) / period
        avg_loss = (avg_loss * (period - 1) + max(-change, 0.0)) / period
        out[index + 1] = value(avg_gain, avg_loss)
    return out


def true_ranges(bars: Sequence[BarLike]) -> list[float]:
    """True range per bar; the first bar has no previous close and uses high - low."""
    out: list[float] = []
    for index, bar in enumerate(bars):
        if index == 0:
            out.append(bar.high - bar.low)
        else:
            prev_close = bars[index - 1].close
            out.append(max(bar.high - bar.low, abs(bar.high - prev_close), abs(bar.low - prev_close)))
    return out


def atr(bars: Sequence[BarLike], period: int = 14) -> list[float | None]:
    """Wilder's ATR: the mean of the first ``period`` true ranges, then Wilder smoothing."""
    ranges = true_ranges(bars)
    out: list[float | None] = [None] * len(ranges)
    if period < 1 or len(ranges) < period:
        return out
    current = sum(ranges[:period]) / period
    out[period - 1] = current
    for index in range(period, len(ranges)):
        current = (current * (period - 1) + ranges[index]) / period
        out[index] = current
    return out


def swings(bars: Sequence[BarLike], strength: int = 2) -> tuple[list[tuple[int, float]], list[tuple[int, float]]]:
    """Confirmed fractal swing highs and lows as ``(index, price)`` lists, oldest first.

    Bar ``i`` is a swing high when its high is above the ``strength`` bars before
    it and not below the ``strength`` bars after it (lows mirrored); a swing is
    confirmed only once those later bars exist.
    """
    highs: list[tuple[int, float]] = []
    lows: list[tuple[int, float]] = []
    for i in range(strength, len(bars) - strength):
        before, after = bars[i - strength : i], bars[i + 1 : i + strength + 1]
        bar = bars[i]
        if all(bar.high > b.high for b in before) and all(bar.high >= b.high for b in after):
            highs.append((i, bar.high))
        if all(bar.low < b.low for b in before) and all(bar.low <= b.low for b in after):
            lows.append((i, bar.low))
    return highs, lows


_NUMBER_RE = re.compile(r"(?<![A-Za-z0-9.,])(\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)(?![A-Za-z0-9])")


def parse_levels(text: str) -> list[float]:
    """Price levels written in free text (e.g. a checklist's key levels), in order, without duplicates.

    Numbers glued to letters (H4, M15, TP1) are ignored; ``2,650.5`` reads as 2650.5.
    """
    levels: list[float] = []
    for match in _NUMBER_RE.finditer(text or ""):
        value = float(match.group(1).replace(",", ""))
        if value > 0 and value not in levels:
            levels.append(value)
    return levels


# --------------------------------------------------------------------------- condition helpers


def _closes(bars: Sequence[BarLike]) -> list[float]:
    return [bar.close for bar in bars]


def _not_enough(needed: int, have: int) -> Result:
    return Result(False, None, f"Not enough data (needs {needed} candles, has {have}).")


# --------------------------------------------------------------------------- condition types


def _ema_trend(bars: Sequence[BarLike], params: dict, context: dict) -> Result:
    fast_n, slow_n = params["fast"], params["slow"]
    closes = _closes(bars)
    fast, slow = ema(closes, fast_n)[-1], ema(closes, slow_n)[-1]
    close = closes[-1]
    details = {"ema_fast": fast, "ema_slow": slow, "close": close}
    values = f"EMA {fast_n} {fmt_price(fast)}, EMA {slow_n} {fmt_price(slow)}, close {fmt_price(close)}"
    if fast > slow and close > slow:
        return Result(
            True,
            "long",
            f"Uptrend: EMA {fast_n} above EMA {slow_n}, price above EMA {slow_n} ({values}).",
            details=details,
        )
    if fast < slow and close < slow:
        return Result(
            True,
            "short",
            f"Downtrend: EMA {fast_n} below EMA {slow_n}, price below EMA {slow_n} ({values}).",
            details=details,
        )
    return Result(False, None, f"No clear trend: EMAs and price disagree ({values}).", details=details)


def _ema_cross(bars: Sequence[BarLike], params: dict, context: dict) -> Result:
    fast_n, slow_n = params["fast"], params["slow"]
    closes = _closes(bars)
    fast, slow = ema(closes, fast_n), ema(closes, slow_n)
    now_diff, prev_diff = fast[-1] - slow[-1], fast[-2] - slow[-2]
    details = {"ema_fast": fast[-1], "ema_slow": slow[-1]}
    values = f"EMA {fast_n} {fmt_price(fast[-1])}, EMA {slow_n} {fmt_price(slow[-1])}"
    if prev_diff <= 0 < now_diff:
        return Result(
            True, "long", f"EMA {fast_n} crossed above EMA {slow_n} on the signal candle ({values}).", details=details
        )
    if prev_diff >= 0 > now_diff:
        return Result(
            True, "short", f"EMA {fast_n} crossed below EMA {slow_n} on the signal candle ({values}).", details=details
        )
    where = "above" if now_diff > 0 else "below"
    return Result(
        False,
        None,
        f"No cross on the signal candle - EMA {fast_n} stays {where} EMA {slow_n} ({values}).",
        details=details,
    )


def _rsi(bars: Sequence[BarLike], params: dict, context: dict) -> Result:
    period, oversold, overbought = params["period"], params["oversold"], params["overbought"]
    value = rsi(_closes(bars), period)[-1]
    details = {"rsi": value}
    shown = f"RSI {period} = {value:.1f}"
    if params["mode"] == "reversal":
        if value <= oversold:
            return Result(
                True, "long", f"{shown} - oversold (at or below {oversold:g}), reversal up expected.", details=details
            )
        if value >= overbought:
            return Result(
                True,
                "short",
                f"{shown} - overbought (at or above {overbought:g}), reversal down expected.",
                details=details,
            )
        return Result(False, None, f"{shown} - between {oversold:g} and {overbought:g}, no extreme.", details=details)
    if 50 < value < overbought:
        return Result(True, "long", f"{shown} - bullish momentum (above 50, below {overbought:g}).", details=details)
    if oversold < value < 50:
        return Result(True, "short", f"{shown} - bearish momentum (below 50, above {oversold:g}).", details=details)
    return Result(False, None, f"{shown} - no momentum signal (flat at 50 or stretched).", details=details)


def _liquidity_sweep(bars: Sequence[BarLike], params: dict, context: dict) -> Result:
    lookback = params["lookback"]
    last, window = bars[-1], bars[-1 - lookback : -1]
    prior_high, prior_low = max(b.high for b in window), min(b.low for b in window)
    details = {"prior_high": prior_high, "prior_low": prior_low}
    swept_low = last.low < prior_low and last.close > prior_low
    swept_high = last.high > prior_high and last.close < prior_high
    if swept_low and swept_high:
        return Result(
            False, None, "The signal candle swept both the high and the low - no clear side.", details=details
        )
    if swept_low:
        return Result(
            True,
            "long",
            f"Sell-side liquidity swept: wick to {fmt_price(last.low)} took out the {lookback}-candle low "
            f"{fmt_price(prior_low)} and closed back above at {fmt_price(last.close)}.",
            stop_hint=last.low,
            details=details,
        )
    if swept_high:
        return Result(
            True,
            "short",
            f"Buy-side liquidity swept: wick to {fmt_price(last.high)} took out the {lookback}-candle high "
            f"{fmt_price(prior_high)} and closed back below at {fmt_price(last.close)}.",
            stop_hint=last.high,
            details=details,
        )
    return Result(
        False,
        None,
        f"No sweep: the {lookback}-candle range {fmt_price(prior_low)} - {fmt_price(prior_high)} was not taken out "
        "with a close back inside.",
        details=details,
    )


def _break_of_structure(bars: Sequence[BarLike], params: dict, context: dict) -> Result:
    strength = params["swing"]
    highs, lows = swings(bars[:-1], strength)
    last, prev = bars[-1], bars[-2]
    if not highs or not lows:
        return Result(False, None, "No confirmed swing high and low yet.")
    swing_high, swing_low = highs[-1][1], lows[-1][1]
    details = {"swing_high": swing_high, "swing_low": swing_low}
    if prev.close <= swing_high < last.close:
        return Result(
            True,
            "long",
            f"Bullish break of structure: closed at {fmt_price(last.close)} above the swing high "
            f"{fmt_price(swing_high)}.",
            stop_hint=swing_low,
            details=details,
        )
    if prev.close >= swing_low > last.close:
        return Result(
            True,
            "short",
            f"Bearish break of structure: closed at {fmt_price(last.close)} below the swing low "
            f"{fmt_price(swing_low)}.",
            stop_hint=swing_high,
            details=details,
        )
    return Result(
        False,
        None,
        f"No fresh break: the signal candle closed inside the swing range {fmt_price(swing_low)} - "
        f"{fmt_price(swing_high)} (or the break happened earlier).",
        details=details,
    )


def _engulfing(bars: Sequence[BarLike], params: dict, context: dict) -> Result:
    prev, last = bars[-2], bars[-1]
    prev_body, last_body = abs(prev.close - prev.open), abs(last.close - last.open)
    bullish = prev.close < prev.open and last.close > last.open and last.close >= prev.open and last.open <= prev.close
    bearish = prev.close > prev.open and last.close < last.open and last.close <= prev.open and last.open >= prev.close
    if bullish and last_body > prev_body:
        return Result(
            True,
            "long",
            "Bullish engulfing: the signal candle's body engulfs the previous bearish body.",
            stop_hint=min(prev.low, last.low),
        )
    if bearish and last_body > prev_body:
        return Result(
            True,
            "short",
            "Bearish engulfing: the signal candle's body engulfs the previous bullish body.",
            stop_hint=max(prev.high, last.high),
        )
    return Result(False, None, "No engulfing candle.")


def _pin_bar(bars: Sequence[BarLike], params: dict, context: dict) -> Result:
    ratio = params["wick_ratio"]
    bar = bars[-1]
    full = bar.high - bar.low
    if full <= 0:
        return Result(False, None, "No pin bar: the signal candle has no range.")
    body = max(abs(bar.close - bar.open), full * 0.02)
    lower, upper = min(bar.open, bar.close) - bar.low, bar.high - max(bar.open, bar.close)
    details = {"body": abs(bar.close - bar.open), "lower_wick": lower, "upper_wick": upper}
    if lower >= ratio * body and lower >= ratio * upper:
        return Result(
            True,
            "long",
            f"Bullish pin bar: lower wick {lower / body:.1f}x the body - buyers rejected the low.",
            stop_hint=bar.low,
            details=details,
        )
    if upper >= ratio * body and upper >= ratio * lower:
        return Result(
            True,
            "short",
            f"Bearish pin bar: upper wick {upper / body:.1f}x the body - sellers rejected the high.",
            stop_hint=bar.high,
            details=details,
        )
    return Result(
        False, None, f"No pin bar: no wick at least {ratio:g}x the body and the opposite wick.", details=details
    )


def _fair_value_gap(bars: Sequence[BarLike], params: dict, context: dict) -> Result:
    lookback = params["lookback"]
    last_index = len(bars) - 1
    last = bars[-1]
    # Newest gap first: candles (a, b, c) with c before the signal candle and within the lookback.
    for c in range(last_index - 1, max(last_index - 1 - lookback, 1), -1):
        a = bars[c - 2]
        later = bars[c + 1 : last_index]
        candle_c = bars[c]
        if a.high < candle_c.low:  # bullish gap: a.high (bottom) .. c.low (top)
            bottom, top = a.high, candle_c.low
            untouched = all(b.low > top for b in later)
            if untouched and last.low <= top and last.close > bottom:
                return Result(
                    True,
                    "long",
                    f"Retest of a bullish fair value gap {fmt_price(bottom)} - {fmt_price(top)}: the signal candle "
                    f"traded into it and closed above its bottom at {fmt_price(last.close)}.",
                    stop_hint=min(bottom, last.low),
                    details={"gap_low": bottom, "gap_high": top},
                )
        if a.low > candle_c.high:  # bearish gap: c.high (bottom) .. a.low (top)
            bottom, top = candle_c.high, a.low
            untouched = all(b.high < bottom for b in later)
            if untouched and last.high >= bottom and last.close < top:
                return Result(
                    True,
                    "short",
                    f"Retest of a bearish fair value gap {fmt_price(bottom)} - {fmt_price(top)}: the signal candle "
                    f"traded into it and closed below its top at {fmt_price(last.close)}.",
                    stop_hint=max(top, last.high),
                    details={"gap_low": bottom, "gap_high": top},
                )
    return Result(False, None, f"No first retest of a fair value gap formed in the last {lookback} candles.")


def _range_breakout(bars: Sequence[BarLike], params: dict, context: dict) -> Result:
    count = params["bars"]
    last, window = bars[-1], bars[-1 - count : -1]
    high, low = max(b.high for b in window), min(b.low for b in window)
    details = {"range_high": high, "range_low": low}
    if last.close > high:
        return Result(
            True,
            "long",
            f"Closed at {fmt_price(last.close)} above the {count}-candle high {fmt_price(high)}.",
            details=details,
        )
    if last.close < low:
        return Result(
            True,
            "short",
            f"Closed at {fmt_price(last.close)} below the {count}-candle low {fmt_price(low)}.",
            details=details,
        )
    return Result(
        False, None, f"Inside the {count}-candle range {fmt_price(low)} - {fmt_price(high)}.", details=details
    )


def _key_level(bars: Sequence[BarLike], params: dict, context: dict) -> Result:
    levels = list(params["levels"]) or list(context.get("key_levels") or [])
    if not levels:
        return Result(False, None, "No key levels: add levels to this condition or to today's pre-market routine.")
    current_atr = atr(bars, 14)[-1]
    tolerance = params["tolerance_atr"] * current_atr
    bar = bars[-1]
    touched = [level for level in levels if bar.low - tolerance <= level <= bar.high + tolerance]
    if not touched:
        nearest = min(levels, key=lambda level: abs(level - bar.close))
        return Result(
            False,
            None,
            f"Not at a key level - nearest is {fmt_price(nearest)}.",
            details={"nearest": nearest, "tolerance": tolerance},
        )
    level = min(touched, key=lambda value: abs(value - bar.close))
    middle = (bar.high + bar.low) / 2
    details = {"level": level, "tolerance": tolerance}
    if bar.low <= level + tolerance and bar.close > level and bar.close >= middle:
        return Result(
            True,
            "long",
            f"Rejected key level {fmt_price(level)} as support (low {fmt_price(bar.low)}, "
            f"closed {fmt_price(bar.close)}).",
            stop_hint=bar.low,
            details=details,
        )
    if bar.high >= level - tolerance and bar.close < level and bar.close <= middle:
        return Result(
            True,
            "short",
            f"Rejected key level {fmt_price(level)} as resistance (high "
            f"{fmt_price(bar.high)}, closed {fmt_price(bar.close)}).",
            stop_hint=bar.high,
            details=details,
        )
    return Result(True, None, f"Price is at key level {fmt_price(level)} (no clear rejection yet).", details=details)


def _atr_filter(bars: Sequence[BarLike], params: dict, context: dict) -> Result:
    period, minimum = params["period"], params["min_atr_pct"]
    value = atr(bars, period)[-1]
    pct = value / bars[-1].close * 100 if bars[-1].close else 0.0
    details = {"atr": value, "atr_pct": pct}
    shown = f"ATR {period} = {fmt_price(value)} ({pct:.3f}% of price)"
    if pct >= minimum:
        return Result(True, None, f"Enough volatility: {shown}, minimum {minimum:g}%.", details=details)
    return Result(False, None, f"Too quiet: {shown}, below the minimum {minimum:g}%.", details=details)


def _p(name, label, kind, default, lo=None, hi=None, choices=(), help=""):
    return ParamSpec(name, label, kind, default, lo, hi, tuple(choices), help)


CONDITION_TYPES: dict[str, ConditionType] = {
    spec.key: spec
    for spec in (
        ConditionType(
            "ema_trend",
            "EMA trend",
            "Fast EMA above the slow EMA with price above the slow EMA (long), or the mirror image (short).",
            True,
            "HTF trend alignment",
            (_p("fast", "Fast EMA", "int", 50, 2, 500), _p("slow", "Slow EMA", "int", 200, 3, 1000)),
            _ema_trend,
            lambda p: p["slow"] + 1,
        ),
        ConditionType(
            "ema_cross",
            "EMA cross",
            "The fast EMA crosses the slow EMA on the signal candle.",
            True,
            "EMA cross",
            (_p("fast", "Fast EMA", "int", 9, 2, 500), _p("slow", "Slow EMA", "int", 21, 3, 1000)),
            _ema_cross,
            lambda p: p["slow"] + 1,
        ),
        ConditionType(
            "rsi",
            "RSI",
            "Reversal: oversold = long, overbought = short. Momentum: above 50 = long, below 50 = short "
            "(not when stretched beyond the thresholds).",
            True,
            "RSI",
            (
                _p("period", "Period", "int", 14, 2, 100),
                _p("oversold", "Oversold", "float", 30, 1, 49),
                _p("overbought", "Overbought", "float", 70, 51, 99),
                _p("mode", "Mode", "choice", "reversal", choices=("reversal", "momentum")),
            ),
            _rsi,
            lambda p: p["period"] + 2,
        ),
        ConditionType(
            "liquidity_sweep",
            "Liquidity sweep",
            "The signal candle's wick takes out the prior high or low of the lookback window and closes back "
            "inside: long after a sweep of the lows, short after a sweep of the highs. Stop beyond the wick.",
            True,
            "Liquidity sweep",
            (_p("lookback", "Lookback (candles)", "int", 20, 3, 200),),
            _liquidity_sweep,
            lambda p: p["lookback"] + 1,
        ),
        ConditionType(
            "break_of_structure",
            "Break of structure",
            "The signal candle closes beyond the latest confirmed swing high (long) or swing low (short). "
            "Stop at the opposite swing.",
            True,
            "Break of structure",
            (_p("swing", "Swing strength (candles each side)", "int", 3, 1, 10),),
            _break_of_structure,
            lambda p: 2 * p["swing"] + 3,
        ),
        ConditionType(
            "engulfing",
            "Engulfing candle",
            "The signal candle's body engulfs the previous opposite-coloured body.",
            True,
            "Engulfing candle",
            (),
            _engulfing,
            lambda p: 2,
        ),
        ConditionType(
            "pin_bar",
            "Pin bar",
            "A long rejection wick: at least the given multiple of the body and of the opposite wick.",
            True,
            "Pin bar",
            (_p("wick_ratio", "Wick / body ratio", "float", 2.0, 1.0, 10.0),),
            _pin_bar,
            lambda p: 1,
        ),
        ConditionType(
            "fair_value_gap",
            "Fair value gap",
            "A three-candle gap formed within the lookback; the signal candle is the first to trade into it and "
            "does not close through it. Stop beyond the gap.",
            True,
            "Order block / FVG",
            (_p("lookback", "Lookback (candles)", "int", 10, 3, 100),),
            _fair_value_gap,
            lambda p: 4,
        ),
        ConditionType(
            "range_breakout",
            "Range breakout",
            "The signal candle closes beyond the high (long) or low (short) of the previous N candles.",
            True,
            "Range breakout",
            (_p("bars", "Range length (candles)", "int", 20, 3, 500),),
            _range_breakout,
            lambda p: p["bars"] + 1,
        ),
        ConditionType(
            "key_level",
            "Key level",
            "Price trades at a key level (within a fraction of ATR). Neutral unless the signal candle rejects it: "
            "support rejection = long, resistance rejection = short. Empty levels use the numbers in today's "
            "pre-market key levels.",
            True,
            "Key level (S/R)",
            (
                _p("levels", "Levels", "levels", (), help="Empty = today's pre-market key levels"),
                _p("tolerance_atr", "Tolerance (x ATR 14)", "float", 0.25, 0.0, 5.0),
            ),
            _key_level,
            lambda p: 15,
        ),
        ConditionType(
            "atr_filter",
            "Volatility filter (ATR)",
            "Neutral filter: ATR as a % of price must be at least the minimum, so dead markets are skipped.",
            False,
            "Volatility (ATR)",
            (
                _p("period", "ATR period", "int", 14, 2, 100),
                _p("min_atr_pct", "Minimum ATR %", "float", 0.05, 0.0, 20.0),
            ),
            _atr_filter,
            lambda p: p["period"] + 1,
        ),
    )
}

MAX_LEVELS = 20


def validate_params(kind: str, params: dict | None) -> dict:
    """Complete ``params`` with defaults and check bounds; raises ValueError with a readable message."""
    spec = CONDITION_TYPES.get(kind)
    if spec is None:
        raise ValueError(f"Unknown condition type '{kind}'")
    params = dict(params or {})
    unknown = set(params) - {p.name for p in spec.params}
    if unknown:
        raise ValueError(f"{spec.label}: unknown parameter {', '.join(sorted(unknown))}")
    out: dict = {}
    for p in spec.params:
        value = params.get(p.name, p.default)
        if p.kind == "choice":
            if value not in p.choices:
                raise ValueError(f"{spec.label}: {p.label} must be one of {', '.join(p.choices)}")
        elif p.kind == "levels":
            if not isinstance(value, (list, tuple)) or len(value) > MAX_LEVELS:
                raise ValueError(f"{spec.label}: {p.label} must be a list of up to {MAX_LEVELS} prices")
            try:
                value = [float(v) for v in value]
            except (TypeError, ValueError):
                raise ValueError(f"{spec.label}: {p.label} must be numbers") from None
            if any(not math.isfinite(v) or v <= 0 for v in value):
                raise ValueError(f"{spec.label}: {p.label} must be positive prices")
        else:
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
                raise ValueError(f"{spec.label}: {p.label} must be a number")
            if p.kind == "int":
                if value != int(value):
                    raise ValueError(f"{spec.label}: {p.label} must be a whole number")
                value = int(value)
            else:
                value = float(value)
            if (p.min is not None and value < p.min) or (p.max is not None and value > p.max):
                raise ValueError(f"{spec.label}: {p.label} must be between {p.min:g} and {p.max:g}")
        out[p.name] = value
    if kind in ("ema_trend", "ema_cross") and out["fast"] >= out["slow"]:
        raise ValueError(f"{spec.label}: the fast EMA must be shorter than the slow EMA")
    return out


def run(kind: str, bars: Sequence[BarLike], params: dict, context: dict | None = None) -> Result:
    """Evaluate one condition on closed ``bars`` (signal candle last); ``params`` are validated first."""
    spec = CONDITION_TYPES[kind]
    params = validate_params(kind, params)
    needed = spec.min_bars(params)
    if len(bars) < needed:
        return _not_enough(needed, len(bars))
    return spec.fn(bars, params, context or {})
