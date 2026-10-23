"""Trade journal endpoints: CRUD, pre-trade check and screenshots."""

from datetime import date
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Response, UploadFile

from ..models import Screenshot, Trade
from ..schemas import ScreenshotOut, ScreenshotUrlIn, TradeCheckOut, TradeIn, TradeListOut, TradeOut, TradeSaveOut
from ..services import trades as trade_service
from ..services import uploads
from ..services.i18n import tr
from .deps import ConfigDep, DbDep, ScopeDep, SettingsDep, get_or_404

router = APIRouter(prefix="/api", tags=["trades"])


def trade_filters(
    scope: ScopeDep,
    status: Literal["open", "closed"] | None = None,
    symbol: str | None = None,
    strategy: str | None = None,
    session: str | None = None,
    direction: Literal["long", "short"] | None = None,
    outcome: Literal["win", "loss", "breakeven"] | None = None,
    emotion: str | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    q: Annotated[str | None, Query(max_length=100)] = None,
) -> trade_service.TradeFilters:
    return trade_service.TradeFilters(
        account_ids=scope.account_ids,
        status=status,
        symbol=symbol,
        strategy=strategy,
        session=session,
        direction=direction,
        outcome=outcome,
        emotion=emotion,
        date_from=date_from,
        date_to=date_to,
        q=q,
    )


FiltersDep = Annotated[trade_service.TradeFilters, Depends(trade_filters)]


def _load(db, trade_id: int) -> Trade:
    trade = trade_service.get_trade(db, trade_id)
    if trade is None:
        raise HTTPException(status_code=404, detail=tr("Trade {trade_id} not found", trade_id=trade_id))
    return trade


@router.get("/trades", response_model=TradeListOut)
def list_trades(
    db: DbDep,
    filters: FiltersDep,
    sort: Literal["entry_time", "close_time", "symbol", "pnl", "r_multiple", "planned_rr"] = "entry_time",
    order: Literal["asc", "desc"] = "desc",
    limit: int = Query(50, ge=1, le=1000),
    offset: int = Query(0, ge=0),
):
    items, total = trade_service.list_trades(
        db, filters, sort=sort, descending=order == "desc", limit=limit, offset=offset
    )
    return {"items": items, "total": total}


@router.post("/trades/check", response_model=TradeCheckOut)
def check_trade(payload: TradeIn, settings: SettingsDep, db: DbDep, trade_id: int | None = None):
    """Evaluate a draft trade against every guardrail without saving it.

    Pass ``trade_id`` when editing, so the check mirrors an update (no blocking,
    recorded daily violations kept) rather than a new entry.
    """
    existing = _load(db, trade_id) if trade_id is not None else None
    ev = trade_service.evaluate(db, settings, payload, existing=existing)
    return {
        "blocked": ev.blocked,
        "violations": [v.to_dict() for v in ev.violations],
        "planned_rr": ev.planned_rr,
        "risk_amount": ev.risk_amount,
        "risk_pct": ev.risk_pct,
        "session": ev.session,
    }


@router.post("/trades", response_model=TradeSaveOut, status_code=201)
def create_trade(payload: TradeIn, settings: SettingsDep, db: DbDep):
    trade, warnings = trade_service.create_trade(db, settings, payload)
    return {"trade": trade, "warnings": [w.to_dict() for w in warnings]}


@router.get("/trades/{trade_id}", response_model=TradeOut)
def read_trade(trade_id: int, db: DbDep):
    return _load(db, trade_id)


@router.put("/trades/{trade_id}", response_model=TradeSaveOut)
def update_trade(trade_id: int, payload: TradeIn, settings: SettingsDep, db: DbDep):
    trade, warnings = trade_service.update_trade(db, settings, _load(db, trade_id), payload)
    return {"trade": trade, "warnings": [w.to_dict() for w in warnings]}


@router.delete("/trades/{trade_id}", status_code=204)
def delete_trade(trade_id: int, db: DbDep, config: ConfigDep):
    trade_service.delete_trade(db, config.uploads_dir, _load(db, trade_id))
    return Response(status_code=204)


@router.post("/trades/{trade_id}/screenshots", response_model=ScreenshotOut, status_code=201)
async def upload_screenshot(
    trade_id: int,
    db: DbDep,
    config: ConfigDep,
    file: Annotated[UploadFile, File()],
    caption: Annotated[str, Form(max_length=200)] = "",
):
    _load(db, trade_id)
    data = await file.read(config.max_upload_bytes + 1)
    filename = uploads.save_image(config.uploads_dir, data, config.max_upload_bytes)
    shot = Screenshot(trade_id=trade_id, kind="file", filename=filename, caption=caption.strip())
    db.add(shot)
    db.commit()
    return shot


@router.post("/trades/{trade_id}/screenshots/url", response_model=ScreenshotOut, status_code=201)
def add_screenshot_url(trade_id: int, payload: ScreenshotUrlIn, db: DbDep):
    _load(db, trade_id)
    shot = Screenshot(trade_id=trade_id, kind="url", url=payload.url, caption=payload.caption)
    db.add(shot)
    db.commit()
    return shot


@router.delete("/screenshots/{screenshot_id}", status_code=204)
def delete_screenshot(screenshot_id: int, db: DbDep, config: ConfigDep):
    shot = get_or_404(db, Screenshot, screenshot_id)
    filename = shot.filename if shot.kind == "file" else None
    db.delete(shot)
    db.commit()
    if filename:
        uploads.delete_file(config.uploads_dir, filename)
    return Response(status_code=204)
