"""Strategy incubator and backtesting."""

import re
from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, HTTPException, Response
from pydantic import Field, StringConstraints, field_validator
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from ..models import BacktestTrade, Strategy
from ..schemas import FiniteModel
from ..services import backtest
from .deps import DbDep, SettingsDep, get_or_404

router = APIRouter(prefix="/api/backtest", tags=["backtest"])

STATUS = Literal["live", "incubator", "retired"]


class BacktestTradeIn(FiniteModel):
    trade_time: datetime
    symbol: Annotated[str, StringConstraints(max_length=20, strip_whitespace=True, to_upper=True)] = ""
    direction: Literal["long", "short"]
    entry_price: float | None = Field(None, gt=0)
    stop_loss: float | None = Field(None, gt=0)
    take_profit: float | None = Field(None, gt=0)
    exit_price: float | None = Field(None, gt=0)
    r_multiple: float | None = None
    pnl: float | None = None
    session: Annotated[str, StringConstraints(max_length=40, strip_whitespace=True)] = ""
    timeframe: Annotated[str, StringConstraints(max_length=10, strip_whitespace=True)] = ""
    tags: list[Annotated[str, StringConstraints(max_length=40, strip_whitespace=True)]] = Field(
        default_factory=list, max_length=20
    )
    notes: Annotated[str, StringConstraints(max_length=5000)] = ""

    @field_validator("trade_time")
    @classmethod
    def _naive(cls, value: datetime) -> datetime:
        return value.replace(tzinfo=None, microsecond=0)


class BacktestTradeOut(BacktestTradeIn):
    id: int
    strategy_id: int
    planned_rr: float | None
    outcome: str | None


class IncubatorStrategyIn(FiniteModel):
    name: Annotated[str, StringConstraints(min_length=1, max_length=60, strip_whitespace=True)]
    description: Annotated[str, StringConstraints(max_length=5000)] = ""
    rules: Annotated[str, StringConstraints(max_length=20000)] = ""
    timeframes: Annotated[str, StringConstraints(max_length=60, strip_whitespace=True)] = ""


class StatusIn(FiniteModel):
    status: STATUS


class BacktestSettingsIn(FiniteModel):
    backtest_risk_per_trade: float | None = Field(None, gt=0)


class ImportIn(FiniteModel):
    csv: Annotated[str, StringConstraints(max_length=2_000_000)]
    dry_run: bool = False


def _strategy(db, strategy_id: int) -> Strategy:
    return get_or_404(db, Strategy, strategy_id)


def _out(trade: BacktestTrade) -> dict:
    return BacktestTradeOut.model_validate(trade, from_attributes=True).model_dump()


def _save(trade: BacktestTrade, payload: BacktestTradeIn) -> None:
    try:
        values = backtest.derive(payload.model_dump())
    except backtest.BacktestError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    for key, value in values.items():
        setattr(trade, key, value)


@router.get("/strategies")
def list_strategies(settings: SettingsDep, db: DbDep):
    """Every strategy with its status and headline backtest metrics."""
    out = []
    for strategy in db.scalars(select(Strategy).order_by(Strategy.status, Strategy.name)):
        report = backtest.strategy_report(db, settings, strategy)
        m, live = report["metrics"], report["live_metrics"]
        out.append(
            {
                "id": strategy.id,
                "name": strategy.name,
                "status": strategy.status,
                "description": strategy.description,
                "trades": m["trades"],
                "win_rate": m["win_rate"],
                "profit_factor": m["profit_factor"],
                "profit_factor_infinite": m["profit_factor_infinite"],
                "expectancy_r": m["expectancy_r"],
                "net_r": m["net_r"],
                "live_trades": live["trades"],
                "live_expectancy_r": live["expectancy_r"],
                "verdict": report["comparison"]["verdict"]["status"],
            }
        )
    return out


@router.post("/strategies", status_code=201)
def create_strategy(payload: IncubatorStrategyIn, db: DbDep):
    """A new experimental strategy, starting in the incubator."""
    strategy = Strategy(**payload.model_dump(), status="incubator")
    db.add(strategy)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="A strategy with that name already exists") from exc
    return {"id": strategy.id, "name": strategy.name, "status": strategy.status}


@router.put("/strategies/{strategy_id}/status")
def set_status(strategy_id: int, payload: StatusIn, db: DbDep):
    strategy = _strategy(db, strategy_id)
    strategy.status = payload.status
    db.commit()
    return {"id": strategy.id, "status": strategy.status}


@router.put("/strategies/{strategy_id}/settings")
def set_settings(strategy_id: int, payload: BacktestSettingsIn, db: DbDep):
    strategy = _strategy(db, strategy_id)
    strategy.backtest_risk_per_trade = payload.backtest_risk_per_trade
    db.commit()
    return {"id": strategy.id, "backtest_risk_per_trade": strategy.backtest_risk_per_trade}


@router.get("/strategies/{strategy_id}/report")
def report(strategy_id: int, settings: SettingsDep, db: DbDep):
    return backtest.strategy_report(db, settings, _strategy(db, strategy_id))


@router.get("/strategies/{strategy_id}/trades")
def list_trades(strategy_id: int, db: DbDep):
    return [_out(trade) for trade in backtest.list_trades(db, _strategy(db, strategy_id))]


@router.post("/strategies/{strategy_id}/trades", status_code=201)
def add_trade(strategy_id: int, payload: BacktestTradeIn, db: DbDep):
    trade = BacktestTrade(strategy_id=_strategy(db, strategy_id).id)
    _save(trade, payload)
    db.add(trade)
    db.commit()
    return _out(trade)


@router.put("/trades/{trade_id}")
def update_trade(trade_id: int, payload: BacktestTradeIn, db: DbDep):
    trade = get_or_404(db, BacktestTrade, trade_id)
    _save(trade, payload)
    db.commit()
    return _out(trade)


@router.delete("/trades/{trade_id}", status_code=204)
def delete_trade(trade_id: int, db: DbDep):
    db.delete(get_or_404(db, BacktestTrade, trade_id))
    db.commit()
    return Response(status_code=204)


@router.delete("/strategies/{strategy_id}/trades", status_code=204)
def clear_trades(strategy_id: int, db: DbDep):
    for trade in backtest.list_trades(db, _strategy(db, strategy_id)):
        db.delete(trade)
    db.commit()
    return Response(status_code=204)


@router.post("/strategies/{strategy_id}/import")
def import_csv(strategy_id: int, payload: ImportIn, db: DbDep):
    """Batch entry from pasted CSV. ``dry_run`` validates and previews without saving."""
    strategy = _strategy(db, strategy_id)
    try:
        rows, errors = backtest.parse_csv(payload.csv)
    except backtest.BacktestError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if not payload.dry_run:
        for values in rows:
            db.add(BacktestTrade(strategy_id=strategy.id, **values))
        db.commit()
    preview = [{**row, "trade_time": row["trade_time"].isoformat()} for row in rows[:20]]
    return {"valid": len(rows), "imported": 0 if payload.dry_run else len(rows), "errors": errors, "preview": preview}


@router.get("/strategies/{strategy_id}/export.csv")
def export_csv(strategy_id: int, db: DbDep):
    strategy = _strategy(db, strategy_id)
    filename = f"backtest-{re.sub(r'[^a-z0-9]+', '-', strategy.name.lower()).strip('-') or strategy.id}.csv"
    return Response(
        content="﻿" + backtest.export_csv(backtest.list_trades(db, strategy)),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
