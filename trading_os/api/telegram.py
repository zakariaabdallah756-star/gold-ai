"""Telegram signal listener (v1.7): capture signals from a Telegram bot and feed them to the Signals page.

Bot API only — the listener reads what the bot is sent (channels/groups it is in, forwards, direct messages).
The bot token is stored encrypted on this computer and never exported. Changing settings never sends an order;
arming the optional auto-execution needs the typed account name (paper accounts excepted).
"""

from typing import Annotated

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import Field, StringConstraints, field_validator
from sqlalchemy import select

from ..models import TelegramSignal, TradingAccount
from ..schemas import FiniteModel
from ..services import accounts, auto_execute, telegram_listener
from ..services.i18n import tr
from .deps import DbDep, SettingsDep, get_or_404

router = APIRouter(prefix="/api/telegram", tags=["telegram"])

ChatName = Annotated[str, StringConstraints(max_length=80, strip_whitespace=True)]


def _box(request: Request):
    return request.app.state.secrets


class ConfigIn(FiniteModel):
    enabled: bool | None = None
    skip_protected: bool | None = None
    allowed_chats: list[ChatName] | None = Field(None, max_length=50)
    risk_pct: float | None = Field(None, gt=0, le=auto_execute.MAX_RISK_PCT)
    max_trades_per_day: int | None = Field(None, ge=0, le=auto_execute.MAX_DAILY_CAP)
    require_checklist: bool | None = None
    auto_account_id: int | None = None


class TokenIn(FiniteModel):
    token: Annotated[str, StringConstraints(max_length=100, strip_whitespace=True)]


class ArmIn(FiniteModel):
    auto_execute: bool
    confirm: Annotated[str, StringConstraints(max_length=100)] = ""

    @field_validator("confirm")
    @classmethod
    def _strip(cls, value: str) -> str:
        return value.strip()


def _status(db, request, settings):
    listener = telegram_listener.get_listener(db)
    return telegram_listener.status_out(db, _box(request), listener)


@router.get("/status")
def status(request: Request, db: DbDep, settings: SettingsDep):
    return _status(db, request, settings)


@router.put("/config")
def set_config(payload: ConfigIn, request: Request, db: DbDep, settings: SettingsDep):
    listener = telegram_listener.get_listener(db)
    if payload.enabled is not None:
        if payload.enabled and not listener.token_enc:
            raise HTTPException(status_code=409, detail=tr("Add the bot token before turning the listener on"))
        listener.enabled = payload.enabled
    if payload.skip_protected is not None:
        listener.skip_protected = payload.skip_protected
    if payload.allowed_chats is not None:
        listener.allowed_chats = [c for c in payload.allowed_chats if c]
    if payload.risk_pct is not None:
        listener.risk_pct = payload.risk_pct
    if payload.max_trades_per_day is not None:
        listener.max_trades_per_day = payload.max_trades_per_day
    if payload.require_checklist is not None:
        listener.require_checklist = payload.require_checklist
    if payload.auto_account_id is not None:
        account = get_or_404(db, TradingAccount, payload.auto_account_id)
        if account.id != listener.auto_account_id:
            listener.auto_execute = False  # a different account must be armed again
        listener.auto_account_id = account.id
    db.commit()
    return _status(db, request, settings)


@router.put("/token")
def set_token(payload: TokenIn, request: Request, db: DbDep, settings: SettingsDep):
    """Store or remove (empty) the bot token. A non-empty token is validated against Telegram's getMe."""
    listener = telegram_listener.get_listener(db)
    box = _box(request)
    token = payload.token.strip()
    if not token:
        telegram_listener.set_token(db, box, listener, "")
        listener.enabled = False
        db.commit()
        return _status(db, request, settings)
    try:
        info = telegram_listener.check_token(token)
        telegram_listener.set_token(db, box, listener, token)
    except telegram_listener.TelegramError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    listener.bot_username = info["username"]
    db.commit()
    return {**_status(db, request, settings), "bot_name": info.get("name", "")}


@router.post("/poll")
def poll_now(request: Request, db: DbDep, settings: SettingsDep):
    """Fetch new messages on demand (also runs in the background while the listener is on)."""
    try:
        summary = telegram_listener.poll(db, settings, _box(request), auto_hook=auto_execute.run_for_telegram)
    except telegram_listener.TelegramError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return {**summary, "status": _status(db, request, settings)}


@router.post("/arm")
def arm(payload: ArmIn, request: Request, db: DbDep, settings: SettingsDep):
    """Turn auto-execution of captured signals on (typed account name for a real account) or off."""
    listener = telegram_listener.get_listener(db)
    if not payload.auto_execute:
        listener.auto_execute = False
        db.commit()
        return _status(db, request, settings)
    if not listener.auto_account_id:
        raise HTTPException(status_code=409, detail=tr("Choose the account for auto-execution first"))
    account = get_or_404(db, TradingAccount, listener.auto_account_id)
    problem = auto_execute.arming_problem(settings, account)
    if problem:
        raise HTTPException(status_code=409, detail=problem)
    label = accounts.account_label(settings, account)
    if account.connection != "paper" and payload.confirm != label:
        raise HTTPException(
            status_code=422,
            detail=tr(
                "Type the account name exactly ({name}) to let Trading OS place real orders by itself", name=label
            ),
        )
    listener.auto_execute = True
    db.commit()
    return _status(db, request, settings)


@router.get("/signals")
def list_signals(db: DbDep, status_filter: str | None = None, limit: int = 50):
    query = select(TelegramSignal).order_by(TelegramSignal.id.desc()).limit(min(limit, 200))
    if status_filter in ("new", "sent", "dismissed"):
        query = query.where(TelegramSignal.status == status_filter)
    rows = db.scalars(query)
    return {"items": [telegram_listener.signal_out(row) for row in rows]}


@router.post("/signals/{signal_id}/dismiss")
def dismiss(signal_id: int, db: DbDep):
    signal = get_or_404(db, TelegramSignal, signal_id)
    signal.status = "dismissed"
    db.commit()
    return telegram_listener.signal_out(signal)


@router.delete("/signals", status_code=204)
def clear_signals(db: DbDep):
    for row in db.scalars(select(TelegramSignal)):
        db.delete(row)
    db.commit()
    return Response(status_code=204)
