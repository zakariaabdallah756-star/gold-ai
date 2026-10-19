from types import SimpleNamespace

import pytest

from trading_os.services.sizing import (
    SizingError,
    calculate_position_size,
    executed_rr,
    gross_pnl,
    planned_rr,
    round_lots,
)


def spec(**overrides):
    values = dict(
        symbol="XAUUSD",
        base_currency="XAU",
        quote_currency="USD",
        contract_size=100,
        pip_size=0.1,
        min_lot=0.01,
        lot_step=0.01,
        max_lot=100,
    )
    values.update(overrides)
    return SimpleNamespace(**values)


def test_gold_position_size_from_prices():
    result = calculate_position_size(spec(), "USD", 10_000, 1.0, entry=2650, stop=2645, take_profit=2665)
    assert result.direction == "long"
    assert result.stop_pips == pytest.approx(50)
    assert result.pip_value_per_lot == pytest.approx(10)
    assert result.lots == pytest.approx(0.2)
    assert result.max_loss == pytest.approx(100)
    assert result.rr == pytest.approx(3)
    assert result.potential_profit == pytest.approx(300)
    assert result.notional == pytest.approx(0.2 * 100 * 2650)


def test_lots_round_down_never_exceeding_risk():
    result = calculate_position_size(spec(), "USD", 12_485.68, 1.0, entry=2650, stop=2642.5)
    assert result.raw_lots == pytest.approx(0.16648, rel=1e-3)
    assert result.lots == pytest.approx(0.16)
    assert result.max_loss <= result.risk_amount
    assert "Rounded down" in result.notes[0]


def test_stop_too_wide_returns_zero_lots_with_note():
    result = calculate_position_size(spec(), "USD", 500, 0.5, stop_pips=500)
    assert result.lots == 0
    assert "Stop too wide" in result.notes[0]


def test_lots_capped_at_max():
    result = calculate_position_size(spec(max_lot=1), "USD", 1_000_000, 2.0, stop_pips=10)
    assert result.lots == 1
    assert "maximum" in result.notes[0]


def test_base_currency_account_uses_inverse_price():
    usdjpy = spec(symbol="USDJPY", base_currency="USD", quote_currency="JPY", contract_size=100_000, pip_size=0.01)
    result = calculate_position_size(usdjpy, "USD", 10_000, 1.0, entry=150.0, stop=149.70)
    assert result.pip_value_per_lot == pytest.approx(100_000 * 0.01 / 150)
    assert result.lots == pytest.approx(0.5)


def test_cross_currency_needs_fx_rate():
    ger40 = spec(
        symbol="GER40",
        base_currency=None,
        quote_currency="EUR",
        contract_size=1,
        pip_size=1.0,
        min_lot=0.1,
        lot_step=0.1,
    )
    with pytest.raises(SizingError, match="conversion rate"):
        calculate_position_size(ger40, "USD", 10_000, 1.0, stop_pips=40)
    result = calculate_position_size(ger40, "USD", 10_000, 1.0, stop_pips=40, fx_rate=1.1)
    assert result.pip_value_per_lot == pytest.approx(1.1)
    assert result.lots == pytest.approx(2.2)


def test_pips_mode_and_target_pips():
    result = calculate_position_size(spec(), "USD", 10_000, 1.0, stop_pips=25, target_pips=75)
    assert result.direction is None
    assert result.lots == pytest.approx(0.4)
    assert result.rr == pytest.approx(3)


def test_take_profit_on_wrong_side_is_rejected():
    with pytest.raises(SizingError):
        calculate_position_size(spec(), "USD", 10_000, 1.0, entry=2650, stop=2645, take_profit=2640)


def test_round_lots_handles_float_steps():
    assert round_lots(0.29999999, spec()) == pytest.approx(0.29)
    assert round_lots(0.3, spec()) == pytest.approx(0.3)
    assert round_lots(0.005, spec()) == 0


def test_rr_and_pnl_math():
    assert planned_rr(100, 98, 106) == pytest.approx(3)
    assert planned_rr(100, 98, None) is None
    assert executed_rr("short", 100, 102, 97) == pytest.approx(1.5)
    assert executed_rr("long", 100, 98, 99) == pytest.approx(-0.5)
    assert gross_pnl(spec(), "USD", "short", 2650, 2640, 0.5) == pytest.approx(500)
