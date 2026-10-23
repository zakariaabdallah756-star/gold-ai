"""Unit tests of the pure position-management engine and the ledger recompute."""

from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

from trading_os.models import Trade, TradeEvent
from trading_os.services import position_engine as engine
from trading_os.services import positions
from trading_os.services.position_engine import Position, PositionConflict, PositionError, Target

GOLD = SimpleNamespace(pip_size=0.1, min_lot=0.01, lot_step=0.01)
T0 = datetime(2026, 10, 1, 10, 0)
ENTRY = 2650.0


def at(minutes: int) -> datetime:
    return T0 + timedelta(minutes=minutes)


def px(direction: str, offset: float) -> float:
    """Price ``offset`` away from the entry in the favourable direction (mirrors shorts)."""
    return ENTRY + offset if direction == "long" else ENTRY - offset


def make(direction="long", *, size=0.3, splits=(50, 25, 25), stop_offset=-5.0, **settings) -> Position:
    targets = tuple(Target(level, px(direction, 5.0 + 5.0 * level), pct) for level, pct in enumerate(splits, start=1))
    return Position(
        direction=direction,
        entry_price=ENTRY,
        entry_time=T0,
        stop=px(direction, stop_offset),
        size=size,
        remaining=size,
        targets=targets,
        **settings,
    )


def bar(direction: str, favourable: float, adverse: float, last: float | None = None) -> dict:
    """High/low (and optional last price) from favourable/adverse offsets."""
    a, b = px(direction, favourable), px(direction, adverse)
    return {"high": max(a, b), "low": min(a, b), "price": None if last is None else px(direction, last)}


def fills(drafts):
    return [(d.kind, d.lots, d.price) for d in drafts if d.lots]


BOTH = pytest.mark.parametrize("direction", ["long", "short"])


# --------------------------------------------------------------------------- targets & break-even


@BOTH
def test_tp1_partial_close_and_auto_break_even(direction):
    pos = make(direction, be_enabled=True)
    drafts = engine.apply_price(pos, GOLD, when=at(5), price=px(direction, 11))

    assert fills(drafts) == [("tp", 0.15, px(direction, 10))]
    assert drafts[0].target_level == 1 and drafts[0].note == "TP1"
    move = drafts[1]
    assert (move.kind, move.stop_before, move.stop_after) == ("breakeven", px(direction, -5), ENTRY)
    assert pos.remaining == 0.15 and pos.stop == ENTRY and pos.stop_kind == "breakeven"
    assert pos.best_price == px(direction, 11) and pos.last_price == px(direction, 11)


@BOTH
def test_tp2_and_tp3_complete_with_last_target_taking_remainder(direction):
    pos = make(direction, be_enabled=True)
    engine.apply_price(pos, GOLD, when=at(5), price=px(direction, 10))
    tp2 = engine.apply_price(pos, GOLD, when=at(10), price=px(direction, 15))
    assert fills(tp2) == [("tp", 0.07, px(direction, 15))]  # floor(0.3 * 25%) = 0.07
    tp3 = engine.apply_price(pos, GOLD, when=at(15), **bar(direction, 21, 14))
    assert fills(tp3) == [("tp", 0.08, px(direction, 20))]  # the remainder, not 0.07
    assert pos.remaining == 0.0 and pos.is_open is False
    with pytest.raises(PositionConflict):
        engine.apply_price(pos, GOLD, when=at(20), price=px(direction, 25))


@BOTH
def test_one_bar_can_hit_several_targets_in_order(direction):
    pos = make(direction)
    drafts = engine.apply_price(pos, GOLD, when=at(5), **bar(direction, 16, 1))
    assert fills(drafts) == [("tp", 0.15, px(direction, 10)), ("tp", 0.07, px(direction, 15))]
    assert pos.hit_levels == {1, 2} and pos.remaining == 0.08


@BOTH
def test_break_even_offset_locks_in_pips(direction):
    pos = make(direction, be_enabled=True, be_offset_pips=5)
    drafts = engine.apply_price(pos, GOLD, when=at(5), price=px(direction, 10))
    assert drafts[-1].kind == "breakeven" and drafts[-1].note == "Stop to break-even +5 pips"
    assert pos.stop == px(direction, 0.5)  # 5 pips * 0.1


def test_break_even_is_applied_once_and_respects_a_later_manual_stop():
    pos = make(be_enabled=True)
    engine.apply_price(pos, GOLD, when=at(5), price=2661)
    engine.move_stop(pos, stop=2648.0, when=at(6))  # trader gives the trade room again
    assert engine.apply_price(pos, GOLD, when=at(7), price=2662) == []
    assert pos.stop == 2648.0 and pos.stop_kind == "manual"


def test_break_even_without_tp1_hit_does_nothing():
    pos = make(be_enabled=True)
    assert engine.apply_price(pos, GOLD, when=at(5), price=2659.9) == []
    assert pos.stop == 2645.0 and pos.best_price == 2659.9


# --------------------------------------------------------------------------- trailing stop


@BOTH
def test_trailing_stop_ratchets_only_in_the_favourable_direction(direction):
    pos = make(direction, splits=(100,), trail_enabled=True, trail_distance_pips=20, trail_trigger="immediate")
    first = engine.apply_price(pos, GOLD, when=at(1), price=px(direction, 6))
    assert [(d.kind, d.stop_after) for d in first] == [("trail", px(direction, 4))]
    assert engine.apply_price(pos, GOLD, when=at(2), price=px(direction, 5)) == []  # pull-back: no move
    assert pos.stop == px(direction, 4)
    second = engine.apply_price(pos, GOLD, when=at(3), **bar(direction, 8, 4.5))
    assert second[0].stop_before == px(direction, 4) and second[0].stop_after == px(direction, 6)
    assert pos.stop_kind == "trail" and pos.best_price == px(direction, 8)

    stopped = engine.apply_price(pos, GOLD, when=at(4), price=px(direction, 5.9))
    assert fills(stopped) == [("stop", 0.3, px(direction, 6))]
    assert stopped[0].note == "trailing stop"


def test_trailing_with_tp1_trigger_waits_for_tp1():
    pos = make(trail_enabled=True, trail_distance_pips=30)
    assert engine.apply_price(pos, GOLD, when=at(1), price=2659) == []
    drafts = engine.apply_price(pos, GOLD, when=at(2), price=2660.5)
    assert [d.kind for d in drafts] == ["tp", "trail"]
    assert pos.stop == 2657.5  # 2660.5 - 30 pips


def test_trailing_never_loosens_a_tighter_break_even_stop():
    pos = make(be_enabled=True, trail_enabled=True, trail_distance_pips=200)
    drafts = engine.apply_price(pos, GOLD, when=at(1), price=2661)
    assert [d.kind for d in drafts] == ["tp", "breakeven"]  # 2661 - 20 = 2641 would widen the stop


# --------------------------------------------------------------------------- conservative stop handling


@BOTH
def test_stop_is_tested_first_when_one_update_spans_stop_and_target(direction):
    pos = make(direction, be_enabled=True)
    drafts = engine.apply_price(pos, GOLD, when=at(5), **bar(direction, 12, -6))
    assert fills(drafts) == [("stop", 0.3, px(direction, -5))]
    assert drafts[0].note == "initial stop"
    assert pos.hit_levels == set() and pos.is_open is False
    assert pos.worst_price == px(direction, -6)


@BOTH
def test_gap_through_the_stop_fills_at_the_stop_price(direction):
    pos = make(direction)
    drafts = engine.apply_price(pos, GOLD, when=at(5), price=px(direction, -9))
    assert fills(drafts) == [("stop", 0.3, px(direction, -5))]
    assert pos.last_price == px(direction, -9)


@BOTH
def test_last_price_through_a_stop_moved_in_the_same_update_stops_out(direction):
    pos = make(direction, be_enabled=True)
    drafts = engine.apply_price(pos, GOLD, when=at(5), **bar(direction, 11, -1, last=-1))
    assert [d.kind for d in drafts] == ["tp", "breakeven", "stop"]
    assert fills(drafts) == [("tp", 0.15, px(direction, 10)), ("stop", 0.15, ENTRY)]
    assert drafts[-1].note == "break-even stop"


def test_touching_the_stop_exactly_counts_as_hit():
    pos = make()
    assert fills(engine.apply_price(pos, GOLD, when=at(1), low=2645.0, high=2649)) == [("stop", 0.3, 2645.0)]


# --------------------------------------------------------------------------- lot rounding


def test_target_below_min_lot_closes_nothing_but_counts_as_hit():
    pos = make(size=0.02, be_enabled=True)
    tp1 = engine.apply_price(pos, GOLD, when=at(1), price=2660)
    assert fills(tp1) == [("tp", 0.01, 2660.0)]
    tp2 = engine.apply_price(pos, GOLD, when=at(2), price=2665)
    assert [(d.kind, d.lots, d.target_level) for d in tp2] == [("tp", 0.0, 2)]  # floor(0.005) = 0
    assert 2 in pos.hit_levels and pos.remaining == 0.01
    assert fills(engine.apply_price(pos, GOLD, when=at(3), price=2670)) == [("tp", 0.01, 2670.0)]


def test_fill_that_would_leave_less_than_min_lot_closes_everything():
    index = SimpleNamespace(pip_size=1.0, min_lot=0.1, lot_step=0.01)
    pos = make(size=0.25, splits=(70, 30))
    assert fills(engine.apply_price(pos, index, when=at(1), price=2660)) == [("tp", 0.25, 2660.0)]
    assert pos.is_open is False


def test_lot_math_tolerates_float_noise():
    assert engine.floor_lots(0.3 * 50 / 100, 0.01) == 0.15
    assert engine.floor_lots(0.29999999999998, 0.01) == 0.3
    assert engine.target_lots(0.3, 25, GOLD) == 0.07
    assert engine.target_lots(0.01, 50, GOLD) == 0.0


# --------------------------------------------------------------------------- manual actions


def test_partial_close_floors_lots_and_enforces_min_lot_rules():
    pos = make()
    draft = engine.partial_close(pos, GOLD, lots=0.105, price=2655, when=at(1))
    assert (draft.kind, draft.lots, draft.pnl, draft.pnl_manual) == ("partial", 0.1, None, False)
    assert pos.remaining == 0.2 and pos.last_price == 2655 and pos.best_price == 2655

    with pytest.raises(PositionError, match="Only 0.2 lots remain"):
        engine.partial_close(pos, GOLD, lots=0.3, price=2655, when=at(2))
    with pytest.raises(PositionError, match="lot step"):
        engine.partial_close(pos, GOLD, lots=0.004, price=2655, when=at(2))

    index = SimpleNamespace(pip_size=1.0, min_lot=0.1, lot_step=0.01)
    with pytest.raises(PositionError, match="below the minimum lot"):
        engine.partial_close(pos, index, lots=0.15, price=2655, when=at(2))
    with pytest.raises(PositionError, match="at least the minimum lot"):
        engine.partial_close(pos, index, lots=0.05, price=2655, when=at(2))

    manual = engine.partial_close(pos, None, lots=0.123, price=2656, when=at(3), pnl=73.8)
    assert (manual.lots, manual.pnl, manual.pnl_manual) == (0.123, 73.8, True)


def test_partial_close_of_everything_is_a_full_close():
    pos = make()
    draft = engine.partial_close(pos, GOLD, lots=0.3, price=2652, when=at(1), note="flat before news")
    assert (draft.kind, draft.lots, draft.note) == ("close", 0.3, "flat before news")
    assert pos.is_open is False


@BOTH
def test_move_stop_is_validated_against_the_last_price(direction):
    pos = make(direction)
    engine.apply_price(pos, GOLD, when=at(1), price=px(direction, 3))
    with pytest.raises(PositionError, match="must stay (below|above) the last price"):
        engine.move_stop(pos, stop=px(direction, 3), when=at(2))
    with pytest.raises(PositionError, match="already at"):
        engine.move_stop(pos, stop=px(direction, -5), when=at(2))

    draft = engine.move_stop(pos, stop=px(direction, 1), when=at(2))
    assert (draft.kind, draft.stop_before, draft.stop_after, draft.note) == (
        "stop_move",
        px(direction, -5),
        px(direction, 1),
        "Stop tightened",
    )
    assert pos.stop_kind == "manual"
    assert engine.move_stop(pos, stop=px(direction, -2), when=at(3)).note == "Stop widened"
    stopped = engine.apply_price(pos, GOLD, when=at(4), price=px(direction, -2))
    assert stopped[0].note == "manual stop"


def test_actions_need_open_position_size_and_ordered_times():
    sizeless = make(size=None)
    sizeless.remaining = None
    with pytest.raises(PositionError, match="position size"):
        engine.apply_price(sizeless, GOLD, when=at(1), price=2651)
    assert engine.move_stop(sizeless, stop=2646, when=at(1)).stop_after == 2646  # no fill needed

    pos = make()
    with pytest.raises(PositionError, match="before the entry"):
        engine.apply_price(pos, GOLD, when=at(-1), price=2651)
    engine.partial_close(pos, GOLD, lots=0.1, price=2652, when=at(10))
    with pytest.raises(PositionError, match="events must be recorded in order"):
        engine.close_remainder(pos, price=2653, when=at(9))
    engine.close_remainder(pos, price=2653, when=at(10))
    with pytest.raises(PositionConflict):
        engine.move_stop(pos, stop=2649, when=at(11))


def test_price_update_validation():
    pos = make()
    with pytest.raises(PositionError, match="Provide the current price"):
        engine.apply_price(pos, GOLD, when=at(1))
    with pytest.raises(PositionError, match="cannot be below low"):
        engine.apply_price(pos, GOLD, when=at(1), high=2650, low=2655)
    with pytest.raises(PositionError, match="must lie between"):
        engine.apply_price(pos, GOLD, when=at(1), price=2660, high=2655, low=2650)
    assert engine.normalize_bar(None, 2655.0, None) == (2655.0, 2655.0, 2655.0)
    assert engine.normalize_bar(None, 2655.0, 2651.0) == (None, 2655.0, 2651.0)


def test_range_update_without_price_keeps_last_price_inside_the_range():
    pos = make()
    engine.apply_price(pos, GOLD, when=at(1), price=2652)
    engine.apply_price(pos, GOLD, when=at(2), high=2658, low=2654)
    assert pos.last_price == 2654 and pos.last_price_at == at(2)
    assert pos.best_price == 2658 and pos.worst_price == 2650  # the entry counts as seen


def test_rebuild_tracking_from_ledger():
    events = [
        SimpleNamespace(time=at(1), price=2660.0),
        SimpleNamespace(time=at(2), price=None),
        SimpleNamespace(time=at(3), price=2648.0),
    ]
    assert engine.rebuild_tracking("long", ENTRY, events) == (2660.0, 2648.0, 2648.0, at(3))
    assert engine.rebuild_tracking("short", ENTRY, events) == (2648.0, 2660.0, 2648.0, at(3))
    assert engine.rebuild_tracking("long", ENTRY, []) == (None, None, None, None)


# --------------------------------------------------------------------------- ledger recompute


SETTINGS = SimpleNamespace(currency="USD")
XAU = SimpleNamespace(
    symbol="XAUUSD",
    base_currency="XAU",
    quote_currency="USD",
    contract_size=100.0,
    pip_size=0.1,
    min_lot=0.01,
    lot_step=0.01,
    max_lot=100.0,
)


def ledger_trade(*events: TradeEvent, **overrides) -> Trade:
    fields = dict(
        status="open",
        entry_time=T0,
        symbol="XAUUSD",
        direction="long",
        entry_price=ENTRY,
        stop_loss=2645.0,
        position_size=0.3,
        risk_amount=150.0,
        fees=4.0,
        events=list(events),
    )
    fields.update(overrides)
    return Trade(**fields)


def fill(minutes, kind, lots, price, pnl=None, **extra) -> TradeEvent:
    return TradeEvent(time=at(minutes), kind=kind, lots=lots, price=price, pnl=pnl, pnl_manual=pnl is not None, **extra)


def test_recompute_partial_fill_keeps_trade_open_with_reduced_risk():
    move = TradeEvent(time=at(2), kind="trail", stop_before=2645.0, stop_after=2647.0)
    trade = ledger_trade(fill(1, "partial", 0.1, 2655.0), move)
    positions.recompute(trade, XAU, SETTINGS)
    assert trade.managed and trade.status == "open" and trade.pnl is None
    assert trade.remaining_size == 0.2 and trade.realized_pnl == 50.0
    assert trade.current_stop == 2647.0 and trade.remaining_risk == 60.0  # 0.2 lots * 3.0 * 100


def test_recompute_closed_trade_derives_exit_pnl_and_r():
    trade = ledger_trade(fill(1, "tp", 0.15, 2660.0, target_level=1), fill(5, "close", 0.15, 2650.0, pnl=-2.5))
    positions.recompute(trade, XAU, SETTINGS)
    assert trade.status == "closed" and trade.exit_time == at(5)
    assert trade.exit_price == 2655.0  # lots-weighted average
    assert trade.realized_pnl == 147.5 and trade.pnl == 143.5  # fills - 4.00 fees
    assert trade.pnl_manual is True and trade.outcome == "win"
    assert trade.executed_rr == 1.0 and trade.r_multiple == round(143.5 / 150, 4)
    assert trade.remaining_size == 0.0 and trade.remaining_risk == 0.0


def test_recompute_unmanaged_trade_uses_the_current_stop_for_risk():
    trade = ledger_trade(TradeEvent(time=at(1), kind="stop_move", stop_before=2645.0, stop_after=2650.0))
    positions.recompute(trade, XAU, SETTINGS)
    assert not trade.managed and trade.remaining_size == 0.3 and trade.remaining_risk == 0.0  # risk-free
    unknown = ledger_trade(symbol="FOO")
    positions.recompute(unknown, None, SETTINGS)
    assert unknown.remaining_risk == 150.0  # falls back to the recorded risk


def test_recompute_needs_manual_pnl_when_fills_cannot_be_valued():
    trade = ledger_trade(fill(1, "partial", 0.1, 2655.0), symbol="FOO")
    with pytest.raises(PositionError, match="enter the P&L manually"):
        positions.recompute(trade, None, SETTINGS)
    eur_quoted = SimpleNamespace(**{**vars(XAU), "symbol": "GER40", "base_currency": None, "quote_currency": "EUR"})
    with pytest.raises(PositionError, match="conversion rate"):
        positions.recompute(ledger_trade(fill(1, "partial", 0.1, 2655.0)), eur_quoted, SETTINGS)
