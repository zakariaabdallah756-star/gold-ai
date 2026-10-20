"""Position management: scale-out targets, break-even, trailing stop, fill ledger.

``recompute`` is called by ``services.trades`` after every create/update (and
by the actions below) and keeps the derived position fields consistent:

* ``current_stop``   - active stop: last stop-move event's ``stop_after``, else ``stop_loss``
* ``remaining_size`` - lots still open (``position_size`` minus filled lots)
* ``realized_pnl``   - sum of fill-event P&L (before fees), rounded to cents
* ``remaining_risk`` - money lost if the remainder is stopped at ``current_stop``
  (0 when the stop is at/beyond the entry, i.e. risk-free, and 0 when closed)
* ``managed``        - True when the trade has fill events. Its status, exit
  (lots-weighted average fill price, last fill time), net P&L (fills - fees),
  R values and outcome then derive from the ledger.

Unmanaged trades keep the legacy behaviour: they are closed through the trade
form with an exit price / P&L and have no fills.

The actions (price updates, partial/full closes, stop moves, undo) validate
with the pure ``position_engine``, append ``TradeEvent`` rows, recompute and
commit. They raise ``PositionError`` (HTTP 422) or ``PositionConflict`` (409)
and leave the database untouched on failure. Event times are naive
account-local datetimes; when omitted they default to "now" (never earlier
than the entry or the latest event).
"""

from __future__ import annotations

import math
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import AccountSettings, Market, Trade, TradeEvent
from . import position_engine as engine
from .account import local_now
from .position_engine import EPSILON, PositionConflict, PositionError, outcome_for
from .sizing import conversion_hint, executed_rr, gross_pnl, risk_for_lots

# --------------------------------------------------------------------------- ledger views


def ordered_events(trade: Trade) -> list[TradeEvent]:
    """Events in ledger order (time, then id; unsaved events last)."""
    return sorted(trade.events, key=lambda e: (e.time, e.id if e.id is not None else math.inf))


def has_fills(trade: Trade) -> bool:
    return any(event.is_fill for event in trade.events)


def filled_lots(trade: Trade) -> float:
    return round(sum(event.lots or 0.0 for event in trade.events if event.is_fill), engine.LOT_DECIMALS)


def plan_targets(trade: Trade) -> tuple[engine.Target, ...]:
    """The scale-out plan; a trade without targets uses its take profit as one 100% target."""
    if trade.targets:
        return tuple(engine.Target(t.level, t.price, t.close_pct) for t in sorted(trade.targets, key=lambda t: t.level))
    if trade.take_profit is not None:
        return (engine.Target(1, trade.take_profit, 100.0),)
    return ()


def stop_kind(trade: Trade) -> str:
    """``initial`` | ``breakeven`` | ``trail`` | ``manual`` - who set the stop in force."""
    moves = [event for event in ordered_events(trade) if event.stop_after is not None]
    return engine.stop_kind_for(moves[-1].kind if moves else None)


def to_position(trade: Trade) -> engine.Position:
    """Engine snapshot of ``trade`` built from its columns and event ledger."""
    events = ordered_events(trade)
    moves = [event for event in events if event.stop_after is not None]
    size = trade.position_size or None
    remaining = max(round(size - filled_lots(trade), engine.LOT_DECIMALS), 0.0) if size else None
    return engine.Position(
        direction=trade.direction,
        entry_price=trade.entry_price,
        entry_time=trade.entry_time,
        stop=moves[-1].stop_after if moves else trade.stop_loss,
        size=size,
        remaining=remaining,
        is_open=trade.status == "open",
        stop_kind=engine.stop_kind_for(moves[-1].kind if moves else None),
        targets=plan_targets(trade),
        hit_levels={event.target_level for event in events if event.kind == "tp" and event.target_level},
        be_enabled=trade.be_enabled,
        be_offset_pips=trade.be_offset_pips or 0.0,
        be_done=any(event.kind == "breakeven" for event in events),
        trail_enabled=trade.trail_enabled,
        trail_distance_pips=trade.trail_distance_pips,
        trail_trigger=trade.trail_trigger,
        best_price=trade.best_price,
        worst_price=trade.worst_price,
        last_price=trade.last_price,
        last_price_at=trade.last_price_at,
        last_event_time=events[-1].time if events else None,
    )


# --------------------------------------------------------------------------- money


def fill_pnl(trade: Trade, market: Market | None, settings: AccountSettings, price: float, lots: float) -> float | None:
    """Gross P&L of closing ``lots`` at ``price`` in account currency (None if not derivable)."""
    if market is None:
        return None
    pnl = gross_pnl(market, settings.currency, trade.direction, trade.entry_price, price, lots)
    return round(pnl, 2) if pnl is not None else None


def _manual_pnl_hint(symbol: str, market: Market | None, settings: AccountSettings) -> str:
    if market is None:
        return f"{symbol} is not in your markets, so fill P&L can't be calculated - enter the P&L manually"
    return conversion_hint(market, settings.currency) + " - or enter the fill P&L manually"


def risk_at_stop(trade: Trade, market: Market | None, settings: AccountSettings, lots: float | None) -> float | None:
    """Money lost if ``lots`` are stopped out at the current stop (0 when risk-free)."""
    if trade.risk_free:
        return 0.0
    stop = trade.current_stop if trade.current_stop is not None else trade.stop_loss
    if market is not None and lots:
        risk = risk_for_lots(market, settings.currency, trade.entry_price, stop, lots)
        if risk is not None:
            return round(risk, 2)
    if trade.risk_amount is None:
        return None
    # Unknown market / conversion: scale the recorded risk by size and stop distance.
    initial = abs(trade.entry_price - trade.stop_loss)
    share = lots / trade.position_size if lots is not None and trade.position_size else 1.0
    distance = abs(trade.entry_price - stop) / initial if initial else 1.0
    return round(trade.risk_amount * share * distance, 2)


def unrealized_pnl(trade: Trade, market: Market | None, settings: AccountSettings) -> float | None:
    """Open P&L of the remainder at ``last_price`` (None when unknown)."""
    lots = trade.remaining_size
    if trade.status != "open" or trade.last_price is None or not lots:
        return None
    return fill_pnl(trade, market, settings, trade.last_price, lots)


# --------------------------------------------------------------------------- recompute


def recompute(trade: Trade, market: Market | None, settings: AccountSettings) -> None:
    """Refresh every derived position field from the trade and its ledger (see module docstring)."""
    events = ordered_events(trade)
    moves = [event for event in events if event.stop_after is not None]
    trade.current_stop = moves[-1].stop_after if moves else trade.stop_loss
    fills = [event for event in events if event.is_fill]
    was_managed, trade.managed = bool(trade.managed), bool(fills)

    if not fills:
        if was_managed:
            # The last fill was undone: the ledger had closed it, so the trade is open again.
            _reopen(trade)
        trade.realized_pnl = 0.0
        if trade.status == "closed":
            trade.remaining_size, trade.remaining_risk = 0.0, 0.0
        else:
            trade.remaining_size = trade.position_size
            trade.remaining_risk = risk_at_stop(trade, market, settings, trade.position_size)
        return

    if not trade.position_size:
        raise PositionError("A trade with recorded fills needs its position size (lots)")
    for event in fills:
        if event.pnl_manual:
            continue
        pnl = fill_pnl(trade, market, settings, event.price, event.lots)
        if pnl is None and event.pnl is None:
            raise PositionError(_manual_pnl_hint(trade.symbol, market, settings))
        if pnl is not None:
            event.pnl = pnl
        # Otherwise keep the value computed when the market was still configured.

    filled = sum(event.lots for event in fills)
    remaining = round(trade.position_size - filled, engine.LOT_DECIMALS)
    trade.remaining_size = remaining if remaining > EPSILON else 0.0
    trade.realized_pnl = round(sum(event.pnl for event in fills), 2)

    if trade.remaining_size > 0:
        _reopen(trade)
        trade.remaining_risk = risk_at_stop(trade, market, settings, trade.remaining_size)
        return

    trade.status = "closed"
    trade.exit_time = fills[-1].time
    trade.exit_price = round(sum(event.price * event.lots for event in fills) / filled, 6)
    trade.pnl = round(trade.realized_pnl - (trade.fees or 0.0), 2)
    trade.pnl_manual = any(event.pnl_manual for event in fills)
    rr = executed_rr(trade.direction, trade.entry_price, trade.stop_loss, trade.exit_price)
    trade.executed_rr = round(rr, 4) if rr is not None else None
    r_multiple = trade.pnl / trade.risk_amount if trade.risk_amount else rr
    trade.r_multiple = round(r_multiple, 4) if r_multiple is not None else None
    trade.outcome = outcome_for(trade.pnl)
    trade.remaining_risk = 0.0


def _reopen(trade: Trade) -> None:
    trade.status = "open"
    trade.exit_time = trade.exit_price = trade.pnl = None
    trade.executed_rr = trade.r_multiple = trade.outcome = None
    trade.pnl_manual = False


def validate_edit(
    trade: Trade,
    market: Market | None,
    settings: AccountSettings,
    *,
    entry_time: datetime,
    symbol: str,
    direction: str,
    entry_price: float,
    position_size: float | None,
) -> None:
    """Reject trade-form edits that would contradict the recorded position events."""
    events = ordered_events(trade)
    if not events:
        return
    if entry_time > events[0].time:
        raise PositionError(f"Entry time can't be after the first position event ({events[0].time:%Y-%m-%d %H:%M})")
    if direction != trade.direction:
        raise PositionError("Direction can't change once the position has events - undo them first")
    fills = [event for event in events if event.is_fill]
    if not fills:
        return
    filled = filled_lots(trade)
    if not position_size or position_size < filled - EPSILON:
        raise PositionError(f"Position size can't be below the {filled:g} lots already closed")
    calculated = [event for event in fills if not event.pnl_manual]
    if calculated and (symbol != trade.symbol or abs(entry_price - trade.entry_price) > EPSILON):
        sample = calculated[0]
        if market is None or gross_pnl(market, settings.currency, direction, entry_price, sample.price, 1.0) is None:
            raise PositionError(
                f"The recorded fills can't be revalued for {symbol} ({_manual_pnl_hint(symbol, market, settings)})"
            )


# --------------------------------------------------------------------------- actions


@contextmanager
def _atomic(db: Session) -> Iterator[None]:
    try:
        yield
    except Exception:
        db.rollback()
        raise


def _market(db: Session, symbol: str) -> Market | None:
    return db.scalar(select(Market).where(Market.symbol == symbol))


def event_time(trade: Trade, settings: AccountSettings, when: datetime | None) -> datetime:
    """``when`` as given, else account-local now - never before the entry or the latest event."""
    if when is not None:
        return when
    return max([local_now(settings), trade.entry_time, *(event.time for event in trade.events)])


def _record(
    trade: Trade, pos: engine.Position, drafts: list[engine.Draft], market: Market | None, settings: AccountSettings
) -> list[TradeEvent]:
    rows = [
        TradeEvent(
            time=d.time,
            kind=d.kind,
            price=d.price,
            lots=d.lots,
            pnl=round(d.pnl, 2) if d.pnl is not None else None,
            pnl_manual=d.pnl_manual,
            target_level=d.target_level,
            stop_before=d.stop_before,
            stop_after=d.stop_after,
            note=d.note[:200],
        )
        for d in drafts
    ]
    trade.events.extend(rows)
    trade.best_price, trade.worst_price = pos.best_price, pos.worst_price
    trade.last_price, trade.last_price_at = pos.last_price, pos.last_price_at
    recompute(trade, market, settings)
    return rows


def _require_market(trade: Trade, market: Market | None) -> Market:
    if market is None:
        raise PositionError(
            f"{trade.symbol} is not in your markets: add it (contract size, pip size) to track prices, "
            "or record closes manually with their P&L"
        )
    return market


def price_update(
    db: Session,
    settings: AccountSettings,
    trade: Trade,
    *,
    price: float | None = None,
    high: float | None = None,
    low: float | None = None,
    when: datetime | None = None,
) -> list[TradeEvent]:
    """Apply a market price to an open trade: stops, targets, break-even, trailing."""
    with _atomic(db):
        market = _require_market(trade, _market(db, trade.symbol))
        pos = to_position(trade)
        drafts = engine.apply_price(
            pos, market, when=event_time(trade, settings, when), price=price, high=high, low=low
        )
        rows = _record(trade, pos, drafts, market, settings)
        db.commit()
    return rows


def symbol_price_update(
    db: Session,
    settings: AccountSettings,
    symbol: str,
    trades: list[Trade],
    *,
    price: float | None = None,
    high: float | None = None,
    low: float | None = None,
    when: datetime | None = None,
) -> list[tuple[Trade, list[TradeEvent], str | None]]:
    """Apply one market price to every open ``trades`` row of ``symbol`` (all or nothing).

    Returns ``(trade, new events, skipped reason)`` per trade. Trades the price
    can't apply to are skipped: no position size, opened after ``when``, or
    with events recorded after ``when``.
    """
    engine.normalize_bar(price, high, low)
    results: list[tuple[Trade, list[TradeEvent], str | None]] = []
    with _atomic(db):
        market = _market(db, symbol)
        if market is None:
            raise PositionError(f"{symbol} is not in your markets: add it to track prices")
        for trade in trades:
            skipped = _skip_reason(trade, when)
            if skipped:
                results.append((trade, [], skipped))
                continue
            pos = to_position(trade)
            drafts = engine.apply_price(
                pos, market, when=event_time(trade, settings, when), price=price, high=high, low=low
            )
            results.append((trade, _record(trade, pos, drafts, market, settings), None))
        db.commit()
    return results


def _skip_reason(trade: Trade, when: datetime | None) -> str | None:
    if not trade.position_size:
        return "No position size set"
    if when is not None and when < trade.entry_time:
        return "Opened after this price"
    if when is not None and any(event.time > when for event in trade.events):
        return "Has position events after this price"
    return None


def partial_close(
    db: Session,
    settings: AccountSettings,
    trade: Trade,
    *,
    lots: float,
    price: float,
    when: datetime | None = None,
    pnl: float | None = None,
    note: str = "",
) -> list[TradeEvent]:
    """Close ``lots`` at ``price`` (``pnl`` overrides the calculated gross fill P&L)."""
    with _atomic(db):
        market = _market(db, trade.symbol)
        pos = to_position(trade)
        draft = engine.partial_close(
            pos, market, lots=lots, price=price, when=event_time(trade, settings, when), pnl=pnl, note=note
        )
        rows = _record(trade, pos, [draft], market, settings)
        db.commit()
    return rows


def close_position(
    db: Session,
    settings: AccountSettings,
    trade: Trade,
    *,
    price: float,
    when: datetime | None = None,
    pnl: float | None = None,
    note: str = "",
) -> list[TradeEvent]:
    """Close the whole remainder at ``price``."""
    with _atomic(db):
        market = _market(db, trade.symbol)
        pos = to_position(trade)
        draft = engine.close_remainder(pos, price=price, when=event_time(trade, settings, when), pnl=pnl, note=note)
        rows = _record(trade, pos, [draft], market, settings)
        db.commit()
    return rows


def move_stop(
    db: Session,
    settings: AccountSettings,
    trade: Trade,
    *,
    stop: float,
    when: datetime | None = None,
    note: str = "",
) -> list[TradeEvent]:
    """Move the active stop manually (validated against the last known price)."""
    with _atomic(db):
        market = _market(db, trade.symbol)
        pos = to_position(trade)
        draft = engine.move_stop(pos, stop=stop, when=event_time(trade, settings, when), note=note)
        rows = _record(trade, pos, [draft], market, settings)
        db.commit()
    return rows


def undo_event(db: Session, settings: AccountSettings, trade: Trade, event: TradeEvent) -> None:
    """Delete ``event`` - only the most recent event of the ledger can be undone.

    Undoing an automatic event (target, stop-out, break-even, trail) also
    rewinds the price tracking to what the remaining ledger supports, so a
    mistyped price can't keep driving the trailing stop.
    """
    events = ordered_events(trade)
    if not events or events[-1].id != event.id:
        raise PositionConflict("Only the most recent position event can be undone - undo the later events first")
    with _atomic(db):
        trade.events.remove(event)
        if event.kind in engine.AUTOMATIC_KINDS:
            best, worst, last, last_at = engine.rebuild_tracking(trade.direction, trade.entry_price, events[:-1])
            trade.best_price, trade.worst_price, trade.last_price, trade.last_price_at = best, worst, last, last_at
        recompute(trade, _market(db, trade.symbol), settings)
        db.commit()


# --------------------------------------------------------------------------- read models


def position_metrics(trade: Trade, market: Market | None, settings: AccountSettings) -> dict:
    """Extra fields of an open position: ``unrealized_pnl``, ``next_target``, ``stop_kind``."""
    hit = {event.target_level for event in trade.events if event.kind == "tp"}
    upcoming = [target for target in plan_targets(trade) if target.level not in hit]
    next_target = upcoming[0] if upcoming else None
    return {
        "unrealized_pnl": unrealized_pnl(trade, market, settings),
        "next_target": (
            {"level": next_target.level, "price": next_target.price, "close_pct": next_target.close_pct}
            if next_target
            else None
        ),
        "stop_kind": stop_kind(trade),
    }
