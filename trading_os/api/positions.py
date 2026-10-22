"""Position management endpoints: price updates, partial closes, stop moves, undo.

Times are optional naive account-local datetimes (aware ones are converted);
when omitted, "now" is used. Fill P&L is gross (before fees) and calculated
from the market's contract spec unless ``pnl`` is given; fees are deducted when
the position is fully closed. Errors: 404 unknown trade/event, 409 action on a
closed position or undo of an older event, 422 invalid action (with ``detail``).
"""

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import Field
from sqlalchemy import select

from ..models import Market, Trade, TradeEvent
from ..schemas import FiniteModel, ShortText, Symbol, TradeEventOut, TradeOut
from ..services import positions as position_service
from ..services import trades as trade_service
from ..services.i18n import tr
from ..services.position_engine import PositionConflict, PositionError
from .deps import DbDep, SettingsDep

router = APIRouter(prefix="/api", tags=["positions"])


# --------------------------------------------------------------------------- schemas


class PriceIn(FiniteModel):
    price: float | None = Field(
        None, gt=0, description="Last/current price (also the high and low when they are omitted)"
    )
    high: float | None = Field(None, gt=0, description="Highest price since the previous update")
    low: float | None = Field(None, gt=0, description="Lowest price since the previous update")
    time: datetime | None = None


class SymbolPriceIn(PriceIn):
    symbol: Symbol


class PartialCloseIn(FiniteModel):
    lots: float = Field(gt=0, description="Lots to close (floored to the lot step)")
    price: float = Field(gt=0)
    time: datetime | None = None
    pnl: float | None = Field(None, description="Gross fill P&L; overrides the calculated value")
    note: ShortText = ""


class CloseIn(FiniteModel):
    price: float = Field(gt=0)
    time: datetime | None = None
    pnl: float | None = Field(None, description="Gross fill P&L; overrides the calculated value")
    note: ShortText = ""


class MoveStopIn(FiniteModel):
    stop: float = Field(gt=0)
    time: datetime | None = None
    note: ShortText = ""


class NextTargetOut(FiniteModel):
    level: int
    price: float
    close_pct: float


class PositionOut(TradeOut):
    unrealized_pnl: float | None
    next_target: NextTargetOut | None
    stop_kind: Literal["initial", "breakeven", "trail", "manual"]


class PositionTotalsOut(FiniteModel):
    open_positions: int
    remaining_risk: float
    unrealized_pnl: float | None
    realized_open: float
    risk_free_count: int


class PositionListOut(FiniteModel):
    positions: list[PositionOut]
    totals: PositionTotalsOut


class TradeEventsOut(FiniteModel):
    trade: TradeOut
    events: list[TradeEventOut]


class TradeOnlyOut(FiniteModel):
    trade: TradeOut


class SymbolPriceResult(FiniteModel):
    trade_id: int
    events: list[TradeEventOut]
    skipped: str | None


class SymbolPriceOut(FiniteModel):
    results: list[SymbolPriceResult]
    trades: list[TradeOut]


# --------------------------------------------------------------------------- helpers


@contextmanager
def _position_errors() -> Iterator[None]:
    try:
        yield
    except PositionConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except PositionError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


def _load(db, trade_id: int) -> Trade:
    trade = trade_service.get_trade(db, trade_id)
    if trade is None:
        raise HTTPException(status_code=404, detail=tr("Trade {trade_id} not found", trade_id=trade_id))
    return trade


def _local(when: datetime | None, settings) -> datetime | None:
    return trade_service.normalize_time(when, settings.timezone)


def _with_events(db, trade_id: int, rows: list[TradeEvent]) -> dict:
    ids = {row.id for row in rows}
    trade = _load(db, trade_id)
    return {"trade": trade, "events": [event for event in trade.events if event.id in ids]}


# --------------------------------------------------------------------------- endpoints


@router.get("/positions", response_model=PositionListOut)
def list_positions(settings: SettingsDep, db: DbDep, symbol: str | None = None):
    """Open positions with live metrics, newest first, plus portfolio totals."""
    filters = trade_service.TradeFilters(status="open", symbol=symbol.strip().upper() if symbol else None)
    trades, _ = trade_service.list_trades(db, filters)
    markets = {market.symbol: market for market in db.scalars(select(Market))}

    positions = []
    for trade in trades:
        metrics = position_service.position_metrics(trade, markets.get(trade.symbol), settings)
        positions.append(PositionOut.model_validate({**TradeOut.model_validate(trade).model_dump(), **metrics}))

    unrealized = [p.unrealized_pnl for p in positions if p.unrealized_pnl is not None]
    totals = {
        "open_positions": len(positions),
        "remaining_risk": round(sum(_open_risk(p) for p in positions), 2),
        "unrealized_pnl": round(sum(unrealized), 2) if unrealized else None,
        "realized_open": round(sum(p.realized_pnl for p in positions), 2),
        "risk_free_count": sum(1 for p in positions if p.risk_free),
    }
    return {"positions": positions, "totals": totals}


def _open_risk(position: PositionOut) -> float:
    if position.remaining_risk is not None:
        return position.remaining_risk
    return position.risk_amount or 0.0


@router.post("/trades/{trade_id}/price", response_model=TradeEventsOut)
def price_update(trade_id: int, payload: PriceIn, settings: SettingsDep, db: DbDep):
    """Apply a market price: stop-outs, target fills, break-even and trailing-stop moves."""
    trade = _load(db, trade_id)
    with _position_errors():
        rows = position_service.price_update(
            db,
            settings,
            trade,
            price=payload.price,
            high=payload.high,
            low=payload.low,
            when=_local(payload.time, settings),
        )
    return _with_events(db, trade_id, rows)


@router.post("/positions/price", response_model=SymbolPriceOut)
def symbol_price_update(payload: SymbolPriceIn, settings: SettingsDep, db: DbDep):
    """Apply one price to every open trade of ``symbol`` (all or nothing)."""
    trades, _ = trade_service.list_trades(
        db, trade_service.TradeFilters(status="open", symbol=payload.symbol), descending=False
    )
    with _position_errors():
        results = position_service.symbol_price_update(
            db,
            settings,
            payload.symbol,
            trades,
            price=payload.price,
            high=payload.high,
            low=payload.low,
            when=_local(payload.time, settings),
        )
    output, refreshed = [], []
    for trade, rows, skipped in results:
        fresh = _with_events(db, trade.id, rows)
        output.append({"trade_id": trade.id, "events": fresh["events"], "skipped": skipped})
        refreshed.append(fresh["trade"])
    return {"results": output, "trades": refreshed}


@router.post("/trades/{trade_id}/partial-close", response_model=TradeEventsOut)
def partial_close(trade_id: int, payload: PartialCloseIn, settings: SettingsDep, db: DbDep):
    """Close part of the position (closing all remaining lots records a full close)."""
    trade = _load(db, trade_id)
    with _position_errors():
        rows = position_service.partial_close(
            db,
            settings,
            trade,
            lots=payload.lots,
            price=payload.price,
            when=_local(payload.time, settings),
            pnl=payload.pnl,
            note=payload.note,
        )
    return _with_events(db, trade_id, rows)


@router.post("/trades/{trade_id}/close", response_model=TradeEventsOut)
def close_position(trade_id: int, payload: CloseIn, settings: SettingsDep, db: DbDep):
    """Close the remainder of the position."""
    trade = _load(db, trade_id)
    with _position_errors():
        rows = position_service.close_position(
            db,
            settings,
            trade,
            price=payload.price,
            when=_local(payload.time, settings),
            pnl=payload.pnl,
            note=payload.note,
        )
    return _with_events(db, trade_id, rows)


@router.post("/trades/{trade_id}/move-stop", response_model=TradeEventsOut)
def move_stop(trade_id: int, payload: MoveStopIn, settings: SettingsDep, db: DbDep):
    """Move the active stop (a long stop must stay below the last price, a short stop above it)."""
    trade = _load(db, trade_id)
    with _position_errors():
        rows = position_service.move_stop(
            db, settings, trade, stop=payload.stop, when=_local(payload.time, settings), note=payload.note
        )
    return _with_events(db, trade_id, rows)


@router.delete("/trades/{trade_id}/events/{event_id}", response_model=TradeOnlyOut)
def undo_event(trade_id: int, event_id: int, settings: SettingsDep, db: DbDep):
    """Undo the most recent position event (409 for older events)."""
    trade = _load(db, trade_id)
    event = next((event for event in trade.events if event.id == event_id), None)
    if event is None:
        raise HTTPException(
            status_code=404,
            detail=tr("Event {event_id} not found on trade {trade_id}", event_id=event_id, trade_id=trade_id),
        )
    with _position_errors():
        position_service.undo_event(db, settings, trade, event)
    return {"trade": _load(db, trade_id)}
