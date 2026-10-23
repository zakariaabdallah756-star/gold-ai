"""Setup detectors: indicator values and every condition type on hand-built candles."""

from datetime import datetime, timedelta

import pytest

from trading_os.services import detectors
from trading_os.services.market_data import Bar


def bars(*rows):
    start = datetime(2026, 10, 20, 8, 0)
    return [Bar(start + timedelta(minutes=15 * i), *row) for i, row in enumerate(rows)]


def closes_to_bars(closes):
    return bars(*[(c, c + 0.5, c - 0.5, c) for c in closes])


def run(kind, candles, context=None, **params):
    return detectors.run(kind, candles, params, context)


# --------------------------------------------------------------------------- indicators


def test_ema_is_seeded_with_the_simple_average():
    assert detectors.ema([1, 2, 3, 4, 5], 3) == [None, None, 2.0, 3.0, 4.0]
    assert detectors.ema([1, 2], 3) == [None, None]


def test_rsi_uses_wilder_smoothing():
    # changes +1 +1 -1 +1: seed gain 1 / loss 0 -> 100; then 0.5/0.5 -> 50; then 0.75/0.25 -> 75
    assert detectors.rsi([1, 2, 3, 2, 3], 2) == [None, None, 100.0, 50.0, 75.0]
    assert detectors.rsi([5, 5, 5], 2)[-1] == 50.0


def test_atr_uses_true_range_and_wilder_smoothing():
    candles = bars((9, 10, 8, 9), (9, 11, 9, 10), (10, 14, 10, 13), (13, 13, 12, 12.5))
    assert detectors.true_ranges(candles) == [2, 2, 4, 1]
    assert detectors.atr(candles, 2) == [None, 2.0, 3.0, 2.0]


def test_swings_are_confirmed_fractals():
    candles = bars((1, 1, 0.5, 1), (1, 3, 0.8, 2), (2, 2, 0.2, 1.5), (1.5, 4, 0.9, 3), (3, 3.5, 0.95, 3))
    highs, lows = detectors.swings(candles, 1)
    assert highs == [(1, 3), (3, 4)]
    assert lows == [(2, 0.2)]


def test_parse_levels_reads_prices_and_ignores_labels():
    text = "Watch 2,650.5 and 2662; H4 FVG 2640-2645, TP1 above. M15 bias up. 2662 again"
    assert detectors.parse_levels(text) == [2650.5, 2662.0, 2640.0, 2645.0]
    assert detectors.parse_levels("") == []


# --------------------------------------------------------------------------- params


def test_validate_params_fills_defaults_and_checks_bounds():
    assert detectors.validate_params("liquidity_sweep", {}) == {"lookback": 20}
    assert detectors.validate_params("rsi", {"mode": "momentum"})["period"] == 14
    with pytest.raises(ValueError, match="Unknown condition type"):
        detectors.validate_params("magic", {})
    with pytest.raises(ValueError, match="between 3 and 200"):
        detectors.validate_params("liquidity_sweep", {"lookback": 500})
    with pytest.raises(ValueError, match="whole number"):
        detectors.validate_params("break_of_structure", {"swing": 2.5})
    with pytest.raises(ValueError, match="unknown parameter"):
        detectors.validate_params("engulfing", {"size": 1})
    with pytest.raises(ValueError, match="fast EMA must be shorter"):
        detectors.validate_params("ema_cross", {"fast": 30, "slow": 21})
    with pytest.raises(ValueError, match="one of"):
        detectors.validate_params("rsi", {"mode": "contrarian"})
    with pytest.raises(ValueError, match="positive prices"):
        detectors.validate_params("key_level", {"levels": [2650, -1]})
    with pytest.raises(ValueError, match="must be a number"):
        detectors.validate_params("pin_bar", {"wick_ratio": "big"})


def test_not_enough_data_is_reported():
    result = run("ema_trend", closes_to_bars([1, 2, 3]), fast=2, slow=5)
    assert not result.met and "Not enough data" in result.label


def test_meta_lists_every_condition_type_with_params():
    meta = [spec.to_dict() for spec in detectors.CONDITION_TYPES.values()]
    keys = {item["key"] for item in meta}
    assert {"ema_trend", "liquidity_sweep", "break_of_structure", "fair_value_gap", "key_level", "atr_filter"} <= keys
    atr_filter = next(item for item in meta if item["key"] == "atr_filter")
    assert atr_filter["directional"] is False
    assert atr_filter["params"][1]["default"] == 0.05


# --------------------------------------------------------------------------- trend & momentum


def test_ema_trend_long_short_and_flat():
    up = run("ema_trend", closes_to_bars(range(1, 40)), fast=5, slow=20)
    assert (up.met, up.direction) == (True, "long") and "Uptrend" in up.label
    down = run("ema_trend", closes_to_bars(range(40, 1, -1)), fast=5, slow=20)
    assert (down.met, down.direction) == (True, "short")
    flat = run("ema_trend", closes_to_bars([10] * 30), fast=5, slow=20)
    assert not flat.met


def test_ema_cross_only_on_the_signal_candle():
    up = run("ema_cross", closes_to_bars([10] * 30 + [12]), fast=3, slow=10)
    assert (up.met, up.direction) == (True, "long")
    down = run("ema_cross", closes_to_bars([10] * 30 + [8]), fast=3, slow=10)
    assert (down.met, down.direction) == (True, "short")
    later = run("ema_cross", closes_to_bars([10] * 30 + [12, 12.5]), fast=3, slow=10)
    assert not later.met and "No cross" in later.label


def test_rsi_reversal_and_momentum_modes():
    falling = closes_to_bars(range(40, 20, -1))
    assert run("rsi", falling, period=14, oversold=30, overbought=70, mode="reversal").direction == "long"
    rising = closes_to_bars(range(20, 40))
    assert run("rsi", rising, period=14, oversold=30, overbought=70, mode="reversal").direction == "short"
    steady = []
    price = 100.0
    for _ in range(10):
        for step in (1, 1, -1):
            price += step
            steady.append(price)
    candles = closes_to_bars(steady)
    momentum = run("rsi", candles, period=14, oversold=30, overbought=70, mode="momentum")
    assert (momentum.met, momentum.direction) == (True, "long") and "bullish momentum" in momentum.label
    assert not run("rsi", candles, period=14, oversold=30, overbought=70, mode="reversal").met


# --------------------------------------------------------------------------- structure

SWEEP_BASE = [(10, 11, 9, 10), (10, 11.5, 9.5, 11), (11, 11.2, 9.8, 10.5)]


def test_liquidity_sweep_of_lows_is_long_with_stop_beyond_the_wick():
    result = run("liquidity_sweep", bars(*SWEEP_BASE, (10.5, 10.8, 8.7, 10.2)), lookback=3)
    assert (result.met, result.direction, result.stop_hint) == (True, "long", 8.7)
    assert "took out the 3-candle low 9.00000" in result.label


def test_liquidity_sweep_of_highs_is_short():
    result = run("liquidity_sweep", bars(*SWEEP_BASE, (10.5, 11.8, 10.1, 11.0)), lookback=3)
    assert (result.met, result.direction, result.stop_hint) == (True, "short", 11.8)


def test_liquidity_sweep_needs_a_close_back_inside():
    assert not run("liquidity_sweep", bars(*SWEEP_BASE, (10.5, 10.8, 8.7, 8.8)), lookback=3).met
    assert not run("liquidity_sweep", bars(*SWEEP_BASE, (10.5, 10.8, 9.6, 10.2)), lookback=3).met


BOS_BASE = [
    (10, 10.5, 9.5, 10),
    (10, 12, 9.8, 11.5),  # swing high 12
    (11.5, 11.6, 10.0, 10.2),
    (10.2, 10.4, 9.0, 9.5),  # swing low 9.0
    (9.5, 11.0, 9.4, 10.8),
    (10.8, 11.9, 10.6, 11.8),
]


def test_break_of_structure_long_with_stop_at_the_opposite_swing():
    result = run("break_of_structure", bars(*BOS_BASE, (11.8, 12.6, 11.7, 12.4)), swing=1)
    assert (result.met, result.direction, result.stop_hint) == (True, "long", 9.0)
    assert result.details == {"swing_high": 12, "swing_low": 9.0}


def test_break_of_structure_short_and_negative():
    base = BOS_BASE[:-1] + [(10.8, 10.9, 9.1, 9.2)]
    short = run("break_of_structure", bars(*base, (9.2, 9.3, 8.5, 8.8)), swing=1)
    # The newest confirmed swing high (11.0) is the opposite swing for the stop.
    assert (short.met, short.direction, short.stop_hint) == (True, "short", 11.0)
    assert not run("break_of_structure", bars(*BOS_BASE, (11.8, 12.3, 11.7, 11.95)), swing=1).met
    # A break that already happened on the previous candle is not fresh.
    stale = BOS_BASE[:-1] + [(10.8, 12.3, 10.6, 12.2)]
    assert not run("break_of_structure", bars(*stale, (12.2, 12.6, 12.1, 12.5)), swing=1).met


def test_engulfing_bullish_bearish_and_negative():
    bull = run("engulfing", bars((10, 10.2, 9.4, 9.5), (9.4, 10.4, 9.3, 10.3)))
    assert (bull.met, bull.direction, bull.stop_hint) == (True, "long", 9.3)
    bear = run("engulfing", bars((9.5, 10.1, 9.4, 10.0), (10.1, 10.2, 9.2, 9.3)))
    assert (bear.met, bear.direction, bear.stop_hint) == (True, "short", 10.2)
    assert not run("engulfing", bars((10, 10.2, 9.4, 9.5), (9.6, 9.9, 9.5, 9.8))).met


def test_pin_bar_needs_a_dominant_wick():
    bull = run("pin_bar", bars((10, 10.2, 9.0, 10.1)), wick_ratio=2.0)
    assert (bull.met, bull.direction, bull.stop_hint) == (True, "long", 9.0)
    bear = run("pin_bar", bars((10.1, 11.2, 9.95, 10.0)), wick_ratio=2.0)
    assert (bear.met, bear.direction, bear.stop_hint) == (True, "short", 11.2)
    assert not run("pin_bar", bars((10, 10.6, 9.4, 10.1)), wick_ratio=2.0).met


FVG_BASE = [
    (10, 10.2, 9.8, 10.1),  # a: high 10.2 = gap bottom
    (10.1, 11.5, 10.0, 11.4),  # displacement
    (11.4, 11.8, 10.6, 11.7),  # c: low 10.6 = gap top
    (11.7, 12.0, 11.2, 11.9),  # does not touch the gap
]


def test_fair_value_gap_first_retest_is_long_with_stop_beyond_the_gap():
    result = run("fair_value_gap", bars(*FVG_BASE, (11.9, 11.95, 10.4, 10.9)), lookback=5)
    assert (result.met, result.direction, result.stop_hint) == (True, "long", 10.2)
    assert result.details == {"gap_low": 10.2, "gap_high": 10.6}


def test_fair_value_gap_negative_cases():
    # Closing through the gap invalidates it.
    assert not run("fair_value_gap", bars(*FVG_BASE, (11.9, 11.95, 10.0, 10.1)), lookback=5).met
    # Not the first touch: an earlier candle already traded into the gap.
    touched = FVG_BASE[:-1] + [(11.7, 12.0, 10.5, 11.9)]
    assert not run("fair_value_gap", bars(*touched, (11.9, 11.95, 10.4, 10.9)), lookback=5).met


def test_fair_value_gap_bearish():
    rows = [(12, 12.2, 11.8, 11.9), (11.9, 12.0, 10.5, 10.6), (10.6, 11.4, 10.2, 10.3), (10.3, 10.8, 10.0, 10.1)]
    result = run("fair_value_gap", bars(*rows, (10.1, 11.6, 10.05, 11.5)), lookback=5)
    assert (result.met, result.direction, result.stop_hint) == (True, "short", 11.8)


def test_range_breakout():
    base = [(10, 10.5, 9.5, 10), (10, 10.6, 9.6, 10.2), (10.2, 10.4, 9.7, 10.1)]
    up = run("range_breakout", bars(*base, (10.1, 10.9, 10.0, 10.8)), bars=3)
    assert (up.met, up.direction) == (True, "long")
    down = run("range_breakout", bars(*base, (10.1, 10.2, 9.2, 9.3)), bars=3)
    assert (down.met, down.direction) == (True, "short")
    assert not run("range_breakout", bars(*base, (10.1, 10.7, 9.8, 10.5)), bars=3).met


FLAT = [(100, 101, 99, 100)] * 15


def test_key_level_rejection_neutral_and_far():
    support = run("key_level", bars(*FLAT, (100, 100.5, 98.8, 100.3)), levels=[99.0], tolerance_atr=0.25)
    assert (support.met, support.direction, support.stop_hint) == (True, "long", 98.8)
    resistance = run("key_level", bars(*FLAT, (100, 101.2, 99.6, 99.7)), levels=[101.0], tolerance_atr=0.25)
    assert (resistance.met, resistance.direction, resistance.stop_hint) == (True, "short", 101.2)
    neutral = run("key_level", bars(*FLAT, (100, 100.5, 99.5, 100.0)), levels=[100.0], tolerance_atr=0.25)
    assert (neutral.met, neutral.direction) == (True, None)
    far = run("key_level", bars(*FLAT, (100, 100.5, 99.5, 100.0)), levels=[120.0], tolerance_atr=0.25)
    assert not far.met and "nearest is 120" in far.label


def test_key_level_falls_back_to_the_checklist_levels():
    candles = bars(*FLAT, (100, 100.5, 98.8, 100.3))
    from_context = run("key_level", candles, {"key_levels": [99.0]}, levels=[], tolerance_atr=0.25)
    assert from_context.direction == "long"
    missing = run("key_level", candles, {"key_levels": []}, levels=[], tolerance_atr=0.25)
    assert not missing.met and "pre-market" in missing.label


def test_atr_filter_is_a_neutral_volatility_filter():
    candles = bars(*FLAT, (100, 101, 99, 100))
    met = run("atr_filter", candles, period=14, min_atr_pct=0.05)
    assert (met.met, met.direction) == (True, None) and met.details["atr_pct"] == pytest.approx(2.0)
    assert not run("atr_filter", candles, period=14, min_atr_pct=5).met
