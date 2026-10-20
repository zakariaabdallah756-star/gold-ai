"""Position-management engine: scale-out targets, break-even, trailing stop (pure, no I/O).

The engine works on a plain :class:`Position` snapshot (built from a trade and
its event ledger by ``services.positions``), mutates it, and returns
:class:`Draft` events for the caller to persist. Fill P&L is valued later by
``services.positions.recompute``; manual P&L passes straight through.

Rules
-----
* **Targets** - ``close_pct`` is a share of the *original* position size. A
  target closes ``floor(size * pct / 100, lot_step)`` lots; when that is below
  the broker's ``min_lot`` it closes nothing but still counts as hit (for
  break-even and trailing). If a fill would leave less than ``min_lot`` open it
  closes everything, and the highest target always closes the whole remainder.
* **Price updates** carry the last ``price`` and/or the ``high``/``low`` seen
  since the previous update. They are processed conservatively: the adverse
  extreme is tested first against the stop in force *before* the update; if it
  touched, the remainder is stopped out and nothing else happens (even if the
  same bar also reached a target). Otherwise targets are hit in level order,
  break-even and the trailing stop are applied, and finally - because the last
  price comes after the bar's extremes - a last price through a stop moved in
  this update stops the remainder out at that stop.
* **No slippage model** - stops and targets fill exactly at their price, also
  when the market gaps through them.
* **Break-even** - once TP1 is hit (and only once per ledger), the stop moves to
  ``entry +/- be_offset_pips`` when that improves it.
* **Trailing stop** - when enabled and triggered (``immediate``, or ``tp1``
  after TP1 is hit) the stop trails ``trail_distance_pips`` behind the best
  price seen since entry and only ever moves in the favourable direction.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime
from decimal import ROUND_DOWN, Decimal
from typing import Protocol

EPSILON = 1e-9
LOT_DECIMALS = 8
PRICE_DECIMALS = 8

FILL_KINDS = frozenset({"tp", "partial", "stop", "close"})
STOP_MOVE_KINDS = frozenset({"breakeven", "trail", "stop_move"})
# Events created by price updates (undoing one rewinds the price tracking too).
AUTOMATIC_KINDS = frozenset({"tp", "stop", "breakeven", "trail"})
STOP_KINDS = ("initial", "breakeven", "trail", "manual")
STOP_KIND_BY_EVENT = {"breakeven": "breakeven", "trail": "trail", "stop_move": "manual"}
STOP_FILL_NOTES = {
    "initial": "initial stop",
    "breakeven": "break-even stop",
    "trail": "trailing stop",
    "manual": "manual stop",
}


class PositionError(ValueError):
    """The requested position action is invalid (HTTP 422)."""


class PositionConflict(PositionError):
    """The action conflicts with the position's state, e.g. it is closed (HTTP 409)."""


class LotSpec(Protocol):
    pip_size: float
    min_lot: float
    lot_step: float


class PricedEvent(Protocol):
    time: datetime
    price: float | None


@dataclass(frozen=True)
class Target:
    level: int
    price: float
    close_pct: float


@dataclass
class Position:
    """Mutable snapshot of an open position and its management settings."""

    direction: str
    entry_price: float
    entry_time: datetime
    stop: float
    size: float | None
    remaining: float | None
    is_open: bool = True
    stop_kind: str = "initial"
    targets: tuple[Target, ...] = ()
    hit_levels: set[int] = field(default_factory=set)
    be_enabled: bool = False
    be_offset_pips: float = 0.0
    be_done: bool = False
    trail_enabled: bool = False
    trail_distance_pips: float | None = None
    trail_trigger: str = "tp1"
    best_price: float | None = None
    worst_price: float | None = None
    last_price: float | None = None
    last_price_at: datetime | None = None
    last_event_time: datetime | None = None

    @property
    def sign(self) -> int:
        return 1 if self.direction == "long" else -1


@dataclass
class Draft:
    """An event to append to the trade's ledger."""

    kind: str
    time: datetime
    price: float | None = None
    lots: float | None = None
    target_level: int | None = None
    stop_before: float | None = None
    stop_after: float | None = None
    pnl: float | None = None
    pnl_manual: bool = False
    note: str = ""


# --------------------------------------------------------------------------- helpers


def outcome_for(pnl: float) -> str:
    """``win`` / ``loss`` / ``breakeven`` from a net P&L rounded to cents."""
    rounded = round(pnl, 2)
    if rounded > 0:
        return "win"
    if rounded < 0:
        return "loss"
    return "breakeven"


def floor_lots(lots: float, step: float) -> float:
    """Floor ``lots`` to a multiple of ``step`` (float noise tolerated)."""
    step_d = Decimal(str(step))
    raw = Decimal(str(round(lots, 9)))
    return float((raw / step_d).to_integral_value(rounding=ROUND_DOWN) * step_d)


def target_lots(size: float, close_pct: float, spec: LotSpec) -> float:
    """Lots a target closes: its share of the original size, floored; 0 below min lot."""
    lots = floor_lots(size * close_pct / 100.0, spec.lot_step)
    return lots if lots >= spec.min_lot - EPSILON else 0.0


def stop_kind_for(event_kind: str | None) -> str:
    """Kind of the stop in force given the last stop-move event kind (``None`` = initial)."""
    return STOP_KIND_BY_EVENT.get(event_kind or "", "initial")


def normalize_bar(price: float | None, high: float | None, low: float | None) -> tuple[float | None, float, float]:
    """Validate a price update and fill in the missing values.

    A single value (``price``, or only one of ``high``/``low``) is an observed
    price: high = low = price. With both extremes and no price the last price
    is unknown (``None`` is returned for it).
    """
    if price is None and (high is None) != (low is None):
        price = high if high is not None else low
    if price is None and high is None:
        raise PositionError("Provide the current price, or the high and low since the last update")
    high = price if high is None else high
    low = price if low is None else low
    if high < low:
        raise PositionError(f"High ({high:g}) cannot be below low ({low:g})")
    if price is not None and not low <= price <= high:
        raise PositionError(f"Price {price:g} must lie between the low ({low:g}) and the high ({high:g})")
    return price, high, low


def check_time(pos: Position, when: datetime) -> None:
    """Events must not precede the entry or the latest recorded event."""
    if when < pos.entry_time:
        raise PositionError(f"Event time {when:%Y-%m-%d %H:%M} is before the entry ({pos.entry_time:%Y-%m-%d %H:%M})")
    if pos.last_event_time is not None and when < pos.last_event_time:
        raise PositionError(
            f"Event time {when:%Y-%m-%d %H:%M} is before the latest position event "
            f"({pos.last_event_time:%Y-%m-%d %H:%M}); events must be recorded in order"
        )


def rebuild_tracking(
    direction: str, entry_price: float, events: Iterable[PricedEvent]
) -> tuple[float | None, float | None, float | None, datetime | None]:
    """Best/worst/last price supported by a (time-ordered) event ledger.

    Used after undoing an automatic event so a mistyped price update cannot
    keep driving the trailing stop. Returns ``(best, worst, last, last_at)``.
    """
    priced = [(event.time, event.price) for event in events if event.price is not None]
    if not priced:
        return None, None, None, None
    sign = 1 if direction == "long" else -1
    prices = [entry_price] + [price for _, price in priced]
    best = max(prices, key=lambda p: p * sign)
    worst = min(prices, key=lambda p: p * sign)
    last_at, last = priced[-1]
    return best, worst, last, last_at


def _price(value: float) -> float:
    return round(value, PRICE_DECIMALS)


def _reached(pos: Position, price: float, level: float) -> bool:
    """``price`` is at or beyond ``level`` in the favourable direction."""
    return (price - level) * pos.sign >= -EPSILON


def _better(pos: Position, price: float, than: float) -> bool:
    """``price`` is strictly more favourable than ``than``."""
    return (price - than) * pos.sign > EPSILON


def _require_open(pos: Position) -> None:
    if not pos.is_open:
        raise PositionConflict("This position is closed - undo its last event to reopen it")


def _require_size(pos: Position) -> None:
    if not pos.size or pos.remaining is None:
        raise PositionError("Set the position size (lots) on this trade before recording fills or price updates")


def _track_best(pos: Position, price: float) -> None:
    """Most favourable price seen since entry (the entry itself counts)."""
    best = pos.entry_price if pos.best_price is None else pos.best_price
    pos.best_price = price if _better(pos, price, best) else best


def _track_worst(pos: Position, price: float) -> None:
    """Most adverse price seen since entry (the entry itself counts)."""
    worst = pos.entry_price if pos.worst_price is None else pos.worst_price
    pos.worst_price = price if _better(pos, worst, price) else worst


def _observe(pos: Position, price: float, when: datetime) -> None:
    """Record a traded price (e.g. a manual fill): extremes and latest price."""
    _track_best(pos, price)
    _track_worst(pos, price)
    _set_last(pos, price, when)


def _set_last(pos: Position, price: float | None, when: datetime) -> None:
    """Latest price; an older observation never replaces a newer one."""
    if price is None or (pos.last_price_at is not None and when < pos.last_price_at):
        return
    pos.last_price, pos.last_price_at = price, when


def _fill(pos: Position, kind: str, when: datetime, price: float, lots: float, **extra) -> Draft:
    closes_all = lots >= pos.remaining - EPSILON
    lots = pos.remaining if closes_all else round(lots, LOT_DECIMALS)
    pos.remaining = 0.0 if closes_all else round(pos.remaining - lots, LOT_DECIMALS)
    pos.is_open = not closes_all
    pos.last_event_time = when
    return Draft(kind=kind, time=when, price=price, lots=lots, **extra)


def _move(pos: Position, kind: str, when: datetime, stop: float, *, price: float | None, note: str) -> Draft:
    draft = Draft(kind=kind, time=when, price=price, stop_before=pos.stop, stop_after=stop, note=note)
    pos.stop = stop
    pos.stop_kind = STOP_KIND_BY_EVENT[kind]
    pos.last_event_time = when
    return draft


def _stop_out(pos: Position, when: datetime) -> Draft:
    return _fill(pos, "stop", when, pos.stop, pos.remaining, note=STOP_FILL_NOTES[pos.stop_kind])


# --------------------------------------------------------------------------- price updates


def apply_price(
    pos: Position,
    spec: LotSpec,
    *,
    when: datetime,
    price: float | None = None,
    high: float | None = None,
    low: float | None = None,
) -> list[Draft]:
    """Process a market price update (see the module docstring for the rules)."""
    _require_open(pos)
    _require_size(pos)
    price, high, low = normalize_bar(price, high, low)
    check_time(pos, when)
    favourable, adverse = (high, low) if pos.sign > 0 else (low, high)

    drafts: list[Draft] = []
    _track_worst(pos, adverse)
    if _reached(pos, pos.stop, adverse):
        # Conservative: the stop in force is assumed to be hit before anything else.
        drafts.append(_stop_out(pos, when))
        _set_last(pos, price if price is not None else drafts[-1].price, when)
        return drafts

    _track_best(pos, favourable)
    drafts += _hit_targets(pos, spec, favourable, when)
    if pos.is_open:
        moves = _break_even(pos, spec, when) + _trail(pos, spec, when)
        drafts += moves
        if moves and price is not None and _reached(pos, pos.stop, price):
            # The last price comes after the extreme that moved the stop: it crossed it.
            drafts.append(_stop_out(pos, when))

    if price is None and pos.last_price is not None:
        price = min(max(pos.last_price, low), high)
    _set_last(pos, price, when)
    return drafts


def _hit_targets(pos: Position, spec: LotSpec, favourable: float, when: datetime) -> list[Draft]:
    drafts: list[Draft] = []
    targets = sorted(pos.targets, key=lambda t: t.level)
    last_level = targets[-1].level if targets else None
    for target in targets:
        if not pos.is_open:
            break
        if target.level in pos.hit_levels or not _reached(pos, favourable, target.price):
            continue
        pos.hit_levels.add(target.level)
        label = f"TP{target.level}"
        if target.level == last_level:
            lots = pos.remaining
        else:
            lots = min(target_lots(pos.size, target.close_pct, spec), pos.remaining)
            if pos.remaining - lots < spec.min_lot - EPSILON:
                lots = pos.remaining
        if lots > EPSILON:
            drafts.append(_fill(pos, "tp", when, target.price, lots, target_level=target.level, note=label))
        else:
            pos.last_event_time = when
            drafts.append(
                Draft(
                    kind="tp",
                    time=when,
                    price=target.price,
                    lots=0.0,
                    target_level=target.level,
                    note=f"{label} reached - share below the minimum lot, nothing closed",
                )
            )
    return drafts


def _break_even(pos: Position, spec: LotSpec, when: datetime) -> list[Draft]:
    if not pos.be_enabled or pos.be_done or 1 not in pos.hit_levels:
        return []
    stop = _price(pos.entry_price + pos.sign * (pos.be_offset_pips or 0.0) * spec.pip_size)
    if not _better(pos, stop, pos.stop) or not _better(pos, pos.best_price, stop):
        return []
    pos.be_done = True
    note = "Stop to break-even" + (f" +{pos.be_offset_pips:g} pips" if pos.be_offset_pips else "")
    return [_move(pos, "breakeven", when, stop, price=pos.best_price, note=note)]


def _trail(pos: Position, spec: LotSpec, when: datetime) -> list[Draft]:
    if not pos.trail_enabled or not pos.trail_distance_pips:
        return []
    if pos.trail_trigger != "immediate" and 1 not in pos.hit_levels:
        return []
    stop = _price(pos.best_price - pos.sign * pos.trail_distance_pips * spec.pip_size)
    if not _better(pos, stop, pos.stop):
        return []
    note = f"Trailing {pos.trail_distance_pips:g} pips behind {pos.best_price:g}"
    return [_move(pos, "trail", when, stop, price=pos.best_price, note=note)]


# --------------------------------------------------------------------------- manual actions


def partial_close(
    pos: Position,
    spec: LotSpec | None,
    *,
    lots: float,
    price: float,
    when: datetime,
    pnl: float | None = None,
    note: str = "",
) -> Draft:
    """Close part of the position at ``price``. Requesting the whole remainder closes it.

    Lots are floored to the lot step; a partial close must be at least the
    minimum lot and leave at least the minimum lot open. ``spec`` is ``None``
    for instruments missing from the trading plan (no lot rules, manual P&L).
    """
    _require_open(pos)
    _require_size(pos)
    check_time(pos, when)
    if lots > pos.remaining + EPSILON:
        raise PositionError(f"Only {pos.remaining:g} lots remain open")
    if lots >= pos.remaining - EPSILON:
        return close_remainder(pos, price=price, when=when, pnl=pnl, note=note)

    lots = floor_lots(lots, spec.lot_step) if spec is not None else round(lots, LOT_DECIMALS)
    if lots <= EPSILON:
        raise PositionError(f"Lot size is below the lot step ({spec.lot_step:g})")
    if spec is not None:
        if lots < spec.min_lot - EPSILON:
            raise PositionError(f"A partial close needs at least the minimum lot ({spec.min_lot:g})")
        left = round(pos.remaining - lots, LOT_DECIMALS)
        if left < spec.min_lot - EPSILON:
            raise PositionError(
                f"That would leave {left:g} lots open, below the minimum lot ({spec.min_lot:g}) - "
                "close the whole position instead"
            )
    _observe(pos, price, when)
    return _fill(pos, "partial", when, price, lots, pnl=pnl, pnl_manual=pnl is not None, note=note or "Partial close")


def close_remainder(
    pos: Position,
    *,
    price: float,
    when: datetime,
    pnl: float | None = None,
    note: str = "",
) -> Draft:
    """Close everything still open at ``price``."""
    _require_open(pos)
    _require_size(pos)
    check_time(pos, when)
    _observe(pos, price, when)
    return _fill(pos, "close", when, price, pos.remaining, pnl=pnl, pnl_manual=pnl is not None, note=note or "Closed")


def move_stop(pos: Position, *, stop: float, when: datetime, note: str = "") -> Draft:
    """Move the stop manually. With a known last price the stop must stay on the losing side of it."""
    _require_open(pos)
    check_time(pos, when)
    stop = _price(stop)
    if abs(stop - pos.stop) <= EPSILON:
        raise PositionError(f"The stop is already at {stop:g}")
    if pos.last_price is not None and _reached(pos, stop, pos.last_price):
        side = "below" if pos.sign > 0 else "above"
        raise PositionError(
            f"A {pos.direction} stop must stay {side} the last price ({pos.last_price:g}) - "
            "close the position instead"
        )
    if not note:
        note = "Stop tightened" if _better(pos, stop, pos.stop) else "Stop widened"
    return _move(pos, "stop_move", when, stop, price=pos.last_price, note=note)
