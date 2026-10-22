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

from .i18n import N_, tr


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
    label: str  # English source text (N_): translated where shown
    kind: str  # int | float | choice | levels
    default: Any
    min: float | None = None
    max: float | None = None
    choices: tuple[str, ...] = ()
    help: str = ""

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "label": tr(self.label),
            "kind": self.kind,
            "default": list(self.default) if isinstance(self.default, (list, tuple)) else self.default,
            "min": self.min,
            "max": self.max,
            "choices": list(self.choices),
            "help": tr(self.help) if self.help else "",
        }


@dataclass(frozen=True)
class ConditionType:
    key: str
    label: str  # English source text (N_): translated where shown
    description: str  # English source text (N_)
    directional: bool  # False = neutral filter that never sets the direction on its own
    confluence: str  # default confluence label (matches the default plan confluences where natural)
    params: tuple[ParamSpec, ...]
    fn: Callable[[Sequence[BarLike], dict, dict], Result]
    min_bars: Callable[[dict], int]

    def to_dict(self) -> dict:
        return {
            "key": self.key,
            "label": tr(self.label),
            "description": tr(self.description),
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
    return Result(False, None, tr("Not enough data (needs {needed} candles, has {have}).", needed=needed, have=have))


# --------------------------------------------------------------------------- condition types


def _ema_trend(bars: Sequence[BarLike], params: dict, context: dict) -> Result:
    fast_n, slow_n = params["fast"], params["slow"]
    closes = _closes(bars)
    fast, slow = ema(closes, fast_n)[-1], ema(closes, slow_n)[-1]
    close = closes[-1]
    details = {"ema_fast": fast, "ema_slow": slow, "close": close}
    values = tr(
        "EMA {fast_n} {fast}, EMA {slow_n} {slow}, close {close}",
        fast_n=fast_n,
        fast=fmt_price(fast),
        slow_n=slow_n,
        slow=fmt_price(slow),
        close=fmt_price(close),
    )
    if fast > slow and close > slow:
        label = tr(
            "Uptrend: EMA {fast_n} above EMA {slow_n}, price above EMA {slow_n} ({values}).",
            fast_n=fast_n,
            slow_n=slow_n,
            values=values,
        )
        return Result(True, "long", label, details=details)
    if fast < slow and close < slow:
        label = tr(
            "Downtrend: EMA {fast_n} below EMA {slow_n}, price below EMA {slow_n} ({values}).",
            fast_n=fast_n,
            slow_n=slow_n,
            values=values,
        )
        return Result(True, "short", label, details=details)
    return Result(
        False, None, tr("No clear trend: EMAs and price disagree ({values}).", values=values), details=details
    )


def _ema_cross(bars: Sequence[BarLike], params: dict, context: dict) -> Result:
    fast_n, slow_n = params["fast"], params["slow"]
    closes = _closes(bars)
    fast, slow = ema(closes, fast_n), ema(closes, slow_n)
    now_diff, prev_diff = fast[-1] - slow[-1], fast[-2] - slow[-2]
    details = {"ema_fast": fast[-1], "ema_slow": slow[-1]}
    values = f"EMA {fast_n} {fmt_price(fast[-1])}, EMA {slow_n} {fmt_price(slow[-1])}"
    shown = {"fast_n": fast_n, "slow_n": slow_n, "values": values}
    if prev_diff <= 0 < now_diff:
        label = tr("EMA {fast_n} crossed above EMA {slow_n} on the signal candle ({values}).", **shown)
        return Result(True, "long", label, details=details)
    if prev_diff >= 0 > now_diff:
        label = tr("EMA {fast_n} crossed below EMA {slow_n} on the signal candle ({values}).", **shown)
        return Result(True, "short", label, details=details)
    if now_diff > 0:
        label = tr("No cross on the signal candle - EMA {fast_n} stays above EMA {slow_n} ({values}).", **shown)
    else:
        label = tr("No cross on the signal candle - EMA {fast_n} stays below EMA {slow_n} ({values}).", **shown)
    return Result(False, None, label, details=details)


def _rsi(bars: Sequence[BarLike], params: dict, context: dict) -> Result:
    period, oversold, overbought = params["period"], params["oversold"], params["overbought"]
    value = rsi(_closes(bars), period)[-1]
    details = {"rsi": value}
    shown = f"RSI {period} = {value:.1f}"
    if params["mode"] == "reversal":
        if value <= oversold:
            label = tr(
                "{shown} - oversold (at or below {oversold:g}), reversal up expected.", shown=shown, oversold=oversold
            )
            return Result(True, "long", label, details=details)
        if value >= overbought:
            label = tr(
                "{shown} - overbought (at or above {overbought:g}), reversal down expected.",
                shown=shown,
                overbought=overbought,
            )
            return Result(True, "short", label, details=details)
        label = tr(
            "{shown} - between {oversold:g} and {overbought:g}, no extreme.",
            shown=shown,
            oversold=oversold,
            overbought=overbought,
        )
        return Result(False, None, label, details=details)
    if 50 < value < overbought:
        label = tr("{shown} - bullish momentum (above 50, below {overbought:g}).", shown=shown, overbought=overbought)
        return Result(True, "long", label, details=details)
    if oversold < value < 50:
        label = tr("{shown} - bearish momentum (below 50, above {oversold:g}).", shown=shown, oversold=oversold)
        return Result(True, "short", label, details=details)
    label = tr("{shown} - no momentum signal (flat at 50 or stretched).", shown=shown)
    return Result(False, None, label, details=details)


def _liquidity_sweep(bars: Sequence[BarLike], params: dict, context: dict) -> Result:
    lookback = params["lookback"]
    last, window = bars[-1], bars[-1 - lookback : -1]
    prior_high, prior_low = max(b.high for b in window), min(b.low for b in window)
    details = {"prior_high": prior_high, "prior_low": prior_low}
    swept_low = last.low < prior_low and last.close > prior_low
    swept_high = last.high > prior_high and last.close < prior_high
    if swept_low and swept_high:
        return Result(
            False,
            None,
            tr("The signal candle swept both the high and the low - no clear side."),
            details=details,
        )
    if swept_low:
        label = tr(
            "Sell-side liquidity swept: wick to {wick} took out the {lookback}-candle low {level} and closed back "
            "above at {close}.",
            wick=fmt_price(last.low),
            lookback=lookback,
            level=fmt_price(prior_low),
            close=fmt_price(last.close),
        )
        return Result(True, "long", label, stop_hint=last.low, details=details)
    if swept_high:
        label = tr(
            "Buy-side liquidity swept: wick to {wick} took out the {lookback}-candle high {level} and closed back "
            "below at {close}.",
            wick=fmt_price(last.high),
            lookback=lookback,
            level=fmt_price(prior_high),
            close=fmt_price(last.close),
        )
        return Result(True, "short", label, stop_hint=last.high, details=details)
    label = tr(
        "No sweep: the {lookback}-candle range {low} - {high} was not taken out with a close back inside.",
        lookback=lookback,
        low=fmt_price(prior_low),
        high=fmt_price(prior_high),
    )
    return Result(False, None, label, details=details)


def _break_of_structure(bars: Sequence[BarLike], params: dict, context: dict) -> Result:
    strength = params["swing"]
    highs, lows = swings(bars[:-1], strength)
    last, prev = bars[-1], bars[-2]
    if not highs or not lows:
        return Result(False, None, tr("No confirmed swing high and low yet."))
    swing_high, swing_low = highs[-1][1], lows[-1][1]
    details = {"swing_high": swing_high, "swing_low": swing_low}
    if prev.close <= swing_high < last.close:
        label = tr(
            "Bullish break of structure: closed at {close} above the swing high {level}.",
            close=fmt_price(last.close),
            level=fmt_price(swing_high),
        )
        return Result(True, "long", label, stop_hint=swing_low, details=details)
    if prev.close >= swing_low > last.close:
        label = tr(
            "Bearish break of structure: closed at {close} below the swing low {level}.",
            close=fmt_price(last.close),
            level=fmt_price(swing_low),
        )
        return Result(True, "short", label, stop_hint=swing_high, details=details)
    label = tr(
        "No fresh break: the signal candle closed inside the swing range {low} - {high} "
        "(or the break happened earlier).",
        low=fmt_price(swing_low),
        high=fmt_price(swing_high),
    )
    return Result(False, None, label, details=details)


def _engulfing(bars: Sequence[BarLike], params: dict, context: dict) -> Result:
    prev, last = bars[-2], bars[-1]
    prev_body, last_body = abs(prev.close - prev.open), abs(last.close - last.open)
    bullish = prev.close < prev.open and last.close > last.open and last.close >= prev.open and last.open <= prev.close
    bearish = prev.close > prev.open and last.close < last.open and last.close <= prev.open and last.open >= prev.close
    if bullish and last_body > prev_body:
        return Result(
            True,
            "long",
            tr("Bullish engulfing: the signal candle's body engulfs the previous bearish body."),
            stop_hint=min(prev.low, last.low),
        )
    if bearish and last_body > prev_body:
        return Result(
            True,
            "short",
            tr("Bearish engulfing: the signal candle's body engulfs the previous bullish body."),
            stop_hint=max(prev.high, last.high),
        )
    return Result(False, None, tr("No engulfing candle."))


def _pin_bar(bars: Sequence[BarLike], params: dict, context: dict) -> Result:
    ratio = params["wick_ratio"]
    bar = bars[-1]
    full = bar.high - bar.low
    if full <= 0:
        return Result(False, None, tr("No pin bar: the signal candle has no range."))
    body = max(abs(bar.close - bar.open), full * 0.02)
    lower, upper = min(bar.open, bar.close) - bar.low, bar.high - max(bar.open, bar.close)
    details = {"body": abs(bar.close - bar.open), "lower_wick": lower, "upper_wick": upper}
    if lower >= ratio * body and lower >= ratio * upper:
        label = tr(
            "Bullish pin bar: lower wick {multiple:.1f}x the body - buyers rejected the low.", multiple=lower / body
        )
        return Result(True, "long", label, stop_hint=bar.low, details=details)
    if upper >= ratio * body and upper >= ratio * lower:
        label = tr(
            "Bearish pin bar: upper wick {multiple:.1f}x the body - sellers rejected the high.", multiple=upper / body
        )
        return Result(True, "short", label, stop_hint=bar.high, details=details)
    label = tr("No pin bar: no wick at least {ratio:g}x the body and the opposite wick.", ratio=ratio)
    return Result(False, None, label, details=details)


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
                label = tr(
                    "Retest of a bullish fair value gap {bottom} - {top}: the signal candle traded into it and "
                    "closed above its bottom at {close}.",
                    bottom=fmt_price(bottom),
                    top=fmt_price(top),
                    close=fmt_price(last.close),
                )
                return Result(
                    True,
                    "long",
                    label,
                    stop_hint=min(bottom, last.low),
                    details={"gap_low": bottom, "gap_high": top},
                )
        if a.low > candle_c.high:  # bearish gap: c.high (bottom) .. a.low (top)
            bottom, top = candle_c.high, a.low
            untouched = all(b.high < bottom for b in later)
            if untouched and last.high >= bottom and last.close < top:
                label = tr(
                    "Retest of a bearish fair value gap {bottom} - {top}: the signal candle traded into it and "
                    "closed below its top at {close}.",
                    bottom=fmt_price(bottom),
                    top=fmt_price(top),
                    close=fmt_price(last.close),
                )
                return Result(
                    True,
                    "short",
                    label,
                    stop_hint=max(top, last.high),
                    details={"gap_low": bottom, "gap_high": top},
                )
    return Result(
        False,
        None,
        tr("No first retest of a fair value gap formed in the last {lookback} candles.", lookback=lookback),
    )


def _range_breakout(bars: Sequence[BarLike], params: dict, context: dict) -> Result:
    count = params["bars"]
    last, window = bars[-1], bars[-1 - count : -1]
    high, low = max(b.high for b in window), min(b.low for b in window)
    details = {"range_high": high, "range_low": low}
    if last.close > high:
        label = tr(
            "Closed at {close} above the {count}-candle high {level}.",
            close=fmt_price(last.close),
            count=count,
            level=fmt_price(high),
        )
        return Result(True, "long", label, details=details)
    if last.close < low:
        label = tr(
            "Closed at {close} below the {count}-candle low {level}.",
            close=fmt_price(last.close),
            count=count,
            level=fmt_price(low),
        )
        return Result(True, "short", label, details=details)
    label = tr("Inside the {count}-candle range {low} - {high}.", count=count, low=fmt_price(low), high=fmt_price(high))
    return Result(False, None, label, details=details)


def _key_level(bars: Sequence[BarLike], params: dict, context: dict) -> Result:
    levels = list(params["levels"]) or list(context.get("key_levels") or [])
    if not levels:
        return Result(False, None, tr("No key levels: add levels to this condition or to today's pre-market routine."))
    current_atr = atr(bars, 14)[-1]
    tolerance = params["tolerance_atr"] * current_atr
    bar = bars[-1]
    touched = [level for level in levels if bar.low - tolerance <= level <= bar.high + tolerance]
    if not touched:
        nearest = min(levels, key=lambda level: abs(level - bar.close))
        return Result(
            False,
            None,
            tr("Not at a key level - nearest is {nearest}.", nearest=fmt_price(nearest)),
            details={"nearest": nearest, "tolerance": tolerance},
        )
    level = min(touched, key=lambda value: abs(value - bar.close))
    middle = (bar.high + bar.low) / 2
    details = {"level": level, "tolerance": tolerance}
    if bar.low <= level + tolerance and bar.close > level and bar.close >= middle:
        label = tr(
            "Rejected key level {level} as support (low {low}, closed {close}).",
            level=fmt_price(level),
            low=fmt_price(bar.low),
            close=fmt_price(bar.close),
        )
        return Result(True, "long", label, stop_hint=bar.low, details=details)
    if bar.high >= level - tolerance and bar.close < level and bar.close <= middle:
        label = tr(
            "Rejected key level {level} as resistance (high {high}, closed {close}).",
            level=fmt_price(level),
            high=fmt_price(bar.high),
            close=fmt_price(bar.close),
        )
        return Result(True, "short", label, stop_hint=bar.high, details=details)
    label = tr("Price is at key level {level} (no clear rejection yet).", level=fmt_price(level))
    return Result(True, None, label, details=details)


def _atr_filter(bars: Sequence[BarLike], params: dict, context: dict) -> Result:
    period, minimum = params["period"], params["min_atr_pct"]
    value = atr(bars, period)[-1]
    pct = value / bars[-1].close * 100 if bars[-1].close else 0.0
    details = {"atr": value, "atr_pct": pct}
    shown = tr("ATR {period} = {value} ({pct:.3f}% of price)", period=period, value=fmt_price(value), pct=pct)
    if pct >= minimum:
        label = tr("Enough volatility: {shown}, minimum {minimum:g}%.", shown=shown, minimum=minimum)
        return Result(True, None, label, details=details)
    label = tr("Too quiet: {shown}, below the minimum {minimum:g}%.", shown=shown, minimum=minimum)
    return Result(False, None, label, details=details)


def _p(name, label, kind, default, lo=None, hi=None, choices=(), help=""):
    return ParamSpec(name, label, kind, default, lo, hi, tuple(choices), help)


# Candles of history a condition may need: providers return up to 500, minus the forming bar and the
# few recent candles the scanner may catch up on.
MAX_HISTORY = 480

CONDITION_TYPES: dict[str, ConditionType] = {
    spec.key: spec
    for spec in (
        ConditionType(
            "ema_trend",
            N_("EMA trend"),
            N_("Fast EMA above the slow EMA with price above the slow EMA (long), or the mirror image (short)."),
            True,
            "HTF trend alignment",
            (
                _p("fast", N_("Fast EMA"), "int", 50, 2, MAX_HISTORY - 2),
                _p("slow", N_("Slow EMA"), "int", 200, 3, MAX_HISTORY - 1),
            ),
            _ema_trend,
            lambda p: p["slow"] + 1,
        ),
        ConditionType(
            "ema_cross",
            N_("EMA cross"),
            N_("The fast EMA crosses the slow EMA on the signal candle."),
            True,
            "EMA cross",
            (
                _p("fast", N_("Fast EMA"), "int", 9, 2, MAX_HISTORY - 2),
                _p("slow", N_("Slow EMA"), "int", 21, 3, MAX_HISTORY - 1),
            ),
            _ema_cross,
            lambda p: p["slow"] + 1,
        ),
        ConditionType(
            "rsi",
            N_("RSI"),
            N_(
                "Reversal: oversold = long, overbought = short. Momentum: above 50 = long, below 50 = short "
                "(not when stretched beyond the thresholds)."
            ),
            True,
            "RSI",
            (
                _p("period", N_("Period"), "int", 14, 2, 100),
                _p("oversold", N_("Oversold"), "float", 30, 1, 49),
                _p("overbought", N_("Overbought"), "float", 70, 51, 99),
                _p("mode", N_("Mode"), "choice", "reversal", choices=("reversal", "momentum")),
            ),
            _rsi,
            lambda p: p["period"] + 2,
        ),
        ConditionType(
            "liquidity_sweep",
            N_("Liquidity sweep"),
            N_(
                "The signal candle's wick takes out the prior high or low of the lookback window and closes back "
                "inside: long after a sweep of the lows, short after a sweep of the highs. Stop beyond the wick."
            ),
            True,
            "Liquidity sweep",
            (_p("lookback", N_("Lookback (candles)"), "int", 20, 3, 200),),
            _liquidity_sweep,
            lambda p: p["lookback"] + 1,
        ),
        ConditionType(
            "break_of_structure",
            N_("Break of structure"),
            N_(
                "The signal candle closes beyond the latest confirmed swing high (long) or swing low (short). "
                "Stop at the opposite swing."
            ),
            True,
            "Break of structure",
            (_p("swing", N_("Swing strength (candles each side)"), "int", 3, 1, 10),),
            _break_of_structure,
            lambda p: 2 * p["swing"] + 3,
        ),
        ConditionType(
            "engulfing",
            N_("Engulfing candle"),
            N_("The signal candle's body engulfs the previous opposite-coloured body."),
            True,
            "Engulfing candle",
            (),
            _engulfing,
            lambda p: 2,
        ),
        ConditionType(
            "pin_bar",
            N_("Pin bar"),
            N_("A long rejection wick: at least the given multiple of the body and of the opposite wick."),
            True,
            "Pin bar",
            (_p("wick_ratio", N_("Wick / body ratio"), "float", 2.0, 1.0, 10.0),),
            _pin_bar,
            lambda p: 1,
        ),
        ConditionType(
            "fair_value_gap",
            N_("Fair value gap"),
            N_(
                "A three-candle gap formed within the lookback; the signal candle is the first to trade into it and "
                "does not close through it. Stop beyond the gap."
            ),
            True,
            "Order block / FVG",
            (_p("lookback", N_("Lookback (candles)"), "int", 10, 3, 100),),
            _fair_value_gap,
            lambda p: 4,
        ),
        ConditionType(
            "range_breakout",
            N_("Range breakout"),
            N_("The signal candle closes beyond the high (long) or low (short) of the previous N candles."),
            True,
            "Range breakout",
            (_p("bars", N_("Range length (candles)"), "int", 20, 3, MAX_HISTORY - 1),),
            _range_breakout,
            lambda p: p["bars"] + 1,
        ),
        ConditionType(
            "key_level",
            N_("Key level"),
            N_(
                "Price trades at a key level (within a fraction of ATR). Neutral unless the signal candle rejects it: "
                "support rejection = long, resistance rejection = short. Empty levels use the numbers in today's "
                "pre-market key levels."
            ),
            True,
            "Key level (S/R)",
            (
                _p("levels", N_("Levels"), "levels", (), help=N_("Empty = today's pre-market key levels")),
                _p("tolerance_atr", N_("Tolerance (x ATR 14)"), "float", 0.25, 0.0, 5.0),
            ),
            _key_level,
            lambda p: 15,
        ),
        ConditionType(
            "atr_filter",
            N_("Volatility filter (ATR)"),
            N_("Neutral filter: ATR as a % of price must be at least the minimum, so dead markets are skipped."),
            False,
            "Volatility (ATR)",
            (
                _p("period", N_("ATR period"), "int", 14, 2, 100),
                _p("min_atr_pct", N_("Minimum ATR %"), "float", 0.05, 0.0, 20.0),
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
        raise ValueError(tr("Unknown condition type '{kind}'", kind=kind))
    params = dict(params or {})
    unknown = set(params) - {p.name for p in spec.params}
    if unknown:
        raise ValueError(
            tr("{condition}: unknown parameter {names}", condition=tr(spec.label), names=", ".join(sorted(unknown)))
        )
    out: dict = {}
    for p in spec.params:
        value = params.get(p.name, p.default)
        if p.kind == "choice":
            if value not in p.choices:
                raise ValueError(
                    tr(
                        "{condition}: {parameter} must be one of {choices}",
                        condition=tr(spec.label),
                        parameter=tr(p.label),
                        choices=", ".join(p.choices),
                    )
                )
        elif p.kind == "levels":
            if not isinstance(value, (list, tuple)) or len(value) > MAX_LEVELS:
                raise ValueError(
                    tr(
                        "{condition}: {parameter} must be a list of up to {maximum} prices",
                        condition=tr(spec.label),
                        parameter=tr(p.label),
                        maximum=MAX_LEVELS,
                    )
                )
            try:
                value = [float(v) for v in value]
            except (TypeError, ValueError):
                raise ValueError(
                    tr("{condition}: {parameter} must be numbers", condition=tr(spec.label), parameter=tr(p.label))
                ) from None
            if any(not math.isfinite(v) or v <= 0 for v in value):
                raise ValueError(
                    tr(
                        "{condition}: {parameter} must be positive prices",
                        condition=tr(spec.label),
                        parameter=tr(p.label),
                    )
                )
        else:
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
                raise ValueError(
                    tr("{condition}: {parameter} must be a number", condition=tr(spec.label), parameter=tr(p.label))
                )
            if p.kind == "int":
                if value != int(value):
                    raise ValueError(
                        tr(
                            "{condition}: {parameter} must be a whole number",
                            condition=tr(spec.label),
                            parameter=tr(p.label),
                        )
                    )
                value = int(value)
            else:
                value = float(value)
            if (p.min is not None and value < p.min) or (p.max is not None and value > p.max):
                raise ValueError(
                    tr(
                        "{condition}: {parameter} must be between {minimum:g} and {maximum:g}",
                        condition=tr(spec.label),
                        parameter=tr(p.label),
                        minimum=p.min,
                        maximum=p.max,
                    )
                )
        out[p.name] = value
    if kind in ("ema_trend", "ema_cross") and out["fast"] >= out["slow"]:
        raise ValueError(tr("{condition}: the fast EMA must be shorter than the slow EMA", condition=tr(spec.label)))
    if spec.min_bars(out) > MAX_HISTORY:
        raise ValueError(
            tr(
                "{condition}: needs more than the {maximum} candles of history the scanner loads",
                condition=tr(spec.label),
                maximum=MAX_HISTORY,
            )
        )
    return out


def run(kind: str, bars: Sequence[BarLike], params: dict, context: dict | None = None) -> Result:
    """Evaluate one condition on closed ``bars`` (signal candle last); ``params`` are validated first."""
    spec = CONDITION_TYPES[kind]
    params = validate_params(kind, params)
    needed = spec.min_bars(params)
    if len(bars) < needed:
        return _not_enough(needed, len(bars))
    return spec.fn(bars, params, context or {})
