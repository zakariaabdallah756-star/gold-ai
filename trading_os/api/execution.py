"""Order routing of trading accounts: connection settings, live order sending, the execution log and the
MetaTrader Expert Advisor's own endpoints.

* ``GET  /api/accounts/{id}/connection``      what the connection panel shows (never a secret)
* ``PUT  /api/accounts/{id}/connection``      ``{connection, connection_config, max_lots_per_order}``
* ``PUT  /api/accounts/{id}/execution``       ``{enabled, confirm}``: on needs the typed account name unless paper
* ``POST /api/accounts/{id}/test``            connection test
* ``GET|POST /api/accounts/{id}/bridge-token`` reveal / rotate the EA token (only on this computer)
* ``GET  /api/executions``                    the execution log
* ``POST /api/execution/orders``              send an order ``{account_id, idempotency_key, order_type, trade}``
* ``POST /api/executions/{id}/cancel``        cancel a pending order
* ``GET  /api/execution/ea/{mq5|mq4}``        the Expert Advisor sources
* ``/api/bridge/*``                           the EA's polling protocol (``services/bridges/mt_bridge.py``),
  authenticated by the account's bridge token - also from another machine when a remote password is set.

Secrets are set with ``PUT /api/accounts/{id}/credentials``; only the bridge token lives here. This module
does not use ``from __future__ import annotations`` (FastAPI resolves the models at import time).
"""

from pathlib import Path
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from pydantic import BaseModel, Field, StringConstraints

from ..models import Execution, TradingAccount
from ..schemas import TradeIn, TradeOut
from ..services import accounts, execution, network, secrets_store
from ..services.bridges import BridgeConfigError, mt_bridge
from ..services.i18n import tr
from .deps import ConfigDep, DbDep, SettingsDep, get_or_404

router = APIRouter(tags=["execution"])

EA_DIR = Path(__file__).resolve().parent.parent / "bridges"
EA_FILES = {"mq5": "TradingOS_Bridge.mq5", "mq4": "TradingOS_Bridge.mq4"}
MAX_BRIDGE_BODY = 16 * 1024
BRIDGE_TOKEN_HEADER = "x-bridge-token"


class ConnectionIn(BaseModel):
    connection: Literal["none", "paper", "webhook", "mt_bridge", "mt5_terminal", "oanda"]
    connection_config: dict = Field(default_factory=dict)
    max_lots_per_order: float | None = Field(None, gt=0, le=10_000, allow_inf_nan=False)


class ExecutionSwitchIn(BaseModel):
    enabled: bool
    confirm: Annotated[str, StringConstraints(max_length=100)] = ""


class OrderIn(BaseModel):
    account_id: int
    idempotency_key: Annotated[str, StringConstraints(min_length=8, max_length=64, pattern=r"^[A-Za-z0-9_.:-]+$")]
    order_type: Literal["market", "limit", "stop"] = "market"
    trade: TradeIn


def _box(request: Request) -> secrets_store.SecretBox:
    return request.app.state.secrets


def _refused(exc: execution.ExecutionError) -> JSONResponse:
    return JSONResponse(status_code=exc.status_code, content={"detail": str(exc), "code": exc.code})


def _local_only(request: Request, config) -> JSONResponse | None:
    if network.is_local_request(request):
        return None
    detail = tr(
        "The bridge token is shown and changed only on the computer running Trading OS (open {url} there).",
        url=network.local_url(config.bind_host, config.port),
    )
    return JSONResponse(status_code=403, content={"detail": detail, "code": "local_only"})


def _panel(request: Request, db, settings, config, account: TradingAccount) -> dict:
    out = execution.connection_out(db, settings, _box(request), account)
    out["can_manage_token"] = network.is_local_request(request)
    out["server_urls"] = {
        "this": str(request.base_url).rstrip("/"),
        "lan": network.lan_urls(config.bind_host, config.port, network.lan_addresses()),
    }
    out["executions"] = [
        execution.execution_out(row) for row in execution.list_executions(db, account_id=account.id, limit=20)
    ]
    out["account"] = accounts.account_out(db, settings, account)
    return out


# --------------------------------------------------------------------------- connection settings


@router.get("/api/accounts/{account_id}/connection")
def get_connection(account_id: int, request: Request, db: DbDep, settings: SettingsDep, config: ConfigDep):
    account = get_or_404(db, TradingAccount, account_id)
    execution.expire_stale(db, settings, account.id)
    return _panel(request, db, settings, config, account)


@router.put("/api/accounts/{account_id}/connection")
def put_connection(
    account_id: int, payload: ConnectionIn, request: Request, db: DbDep, settings: SettingsDep, config: ConfigDep
):
    """How the account is reached. Changing the type, or where orders go, switches order sending off."""
    account = get_or_404(db, TradingAccount, account_id)
    try:
        switched_off = execution.set_connection(
            db, settings, account, payload.connection, payload.connection_config, payload.max_lots_per_order
        )
    except BridgeConfigError as exc:
        db.rollback()
        return JSONResponse(status_code=422, content={"detail": str(exc), "code": "connection_config"})
    except execution.ExecutionError as exc:
        db.rollback()
        return _refused(exc)
    return {**_panel(request, db, settings, config, account), "switched_off": switched_off}


@router.put("/api/accounts/{account_id}/execution")
def put_execution(
    account_id: int,
    payload: ExecutionSwitchIn,
    request: Request,
    db: DbDep,
    settings: SettingsDep,
    config: ConfigDep,
):
    """Switch order sending on (typed account name unless paper trading) or off (always, no questions)."""
    account = get_or_404(db, TradingAccount, account_id)
    try:
        execution.set_execution(db, settings, _box(request), account, payload.enabled, payload.confirm)
    except execution.ExecutionError as exc:
        db.rollback()
        return _refused(exc)
    return _panel(request, db, settings, config, account)


@router.post("/api/accounts/{account_id}/test")
def test_connection(account_id: int, request: Request, db: DbDep, settings: SettingsDep, config: ConfigDep):
    account = get_or_404(db, TradingAccount, account_id)
    try:
        result = execution.test_connection(db, settings, _box(request), account)
    except execution.ExecutionError as exc:
        return _refused(exc)
    return {**result, "panel": _panel(request, db, settings, config, account)}


@router.get("/api/accounts/{account_id}/bridge-token")
def reveal_bridge_token(account_id: int, request: Request, db: DbDep, config: ConfigDep):
    """The EA token in clear - only to a browser on the computer running Trading OS."""
    refused = _local_only(request, config)
    if refused is not None:
        return refused
    account = get_or_404(db, TradingAccount, account_id)
    try:
        token = secrets_store.get(db, _box(request), account.id, "bridge_token")
    except secrets_store.SecretError:
        detail = tr("The bridge token cannot be read on this computer - create a new one")
        return JSONResponse(status_code=409, content={"detail": detail, "code": "secret_unreadable"})
    return {"token": token or None}


@router.post("/api/accounts/{account_id}/bridge-token")
def rotate_bridge_token(account_id: int, request: Request, db: DbDep, config: ConfigDep):
    """A new EA token (the old one stops working at once). Only on the computer running Trading OS."""
    refused = _local_only(request, config)
    if refused is not None:
        return refused
    account = get_or_404(db, TradingAccount, account_id)
    token = secrets_store.new_bridge_token()
    secrets_store.put(db, _box(request), account.id, "bridge_token", token)
    db.commit()
    return {"token": token, "credentials": secrets_store.described(db, account.id)}


# --------------------------------------------------------------------------- orders and the log


@router.get("/api/executions")
def list_executions(
    db: DbDep,
    settings: SettingsDep,
    account_id: int | None = None,
    trade_id: int | None = None,
    limit: int = Query(50, ge=1, le=execution.LOG_LIMIT),
):
    execution.expire_stale(db, settings, account_id)
    rows = execution.list_executions(db, account_id=account_id, trade_id=trade_id, limit=limit)
    return {"items": [execution.execution_out(row) for row in rows]}


@router.post("/api/execution/orders", status_code=201)
def send_order(payload: OrderIn, request: Request, response: Response, db: DbDep, settings: SettingsDep):
    """Send a trade to the account's broker - only ever on the trader's explicit request.

    The same guardrails as ``POST /api/trades`` run first (423 with the violations: nothing is sent). The same
    ``idempotency_key`` returns the stored order (HTTP 200, ``duplicate``) and never sends twice.
    """
    account = get_or_404(db, TradingAccount, payload.account_id)
    try:
        result = execution.submit(
            db, settings, _box(request), account, payload.trade, payload.idempotency_key, order_type=payload.order_type
        )
    except execution.ExecutionError as exc:
        db.rollback()
        return _refused(exc)
    if result.duplicate:
        response.status_code = 200
    return {
        "execution": execution.execution_out(result.execution),
        "trade": TradeOut.model_validate(result.trade) if result.trade is not None else None,
        "warnings": [violation.to_dict() for violation in result.violations],
        "duplicate": result.duplicate,
        "account": {"id": account.id, "name": execution.account_name(settings, account)},
    }


@router.post("/api/executions/{execution_id}/cancel")
def cancel_order(execution_id: int, request: Request, db: DbDep, settings: SettingsDep):
    row = get_or_404(db, Execution, execution_id)
    try:
        execution.cancel(db, settings, _box(request), row)
    except execution.ExecutionError as exc:
        db.rollback()
        return _refused(exc)
    return execution.execution_out(row)


@router.get("/api/execution/ea/{platform}")
def download_ea(platform: Literal["mq5", "mq4"]):
    """The Expert Advisor source for MetaTrader 5 (mq5) or MetaTrader 4 (mq4)."""
    name = EA_FILES[platform]
    return FileResponse(EA_DIR / name, media_type="text/plain; charset=utf-8", filename=name)


# --------------------------------------------------------------------------- the EA's endpoints (/api/bridge)


async def bridge_fields(request: Request) -> dict:
    """``key=value`` pairs of a form or plain-text body (the EA needs no JSON).

    These endpoints answer before any sign-in, so the body is capped while it is read, never after.
    """
    declared = request.headers.get("content-length", "")
    if declared.isdigit() and int(declared) > MAX_BRIDGE_BODY:
        raise HTTPException(status_code=413, detail=tr("Request too large"))
    raw = bytearray()
    async for chunk in request.stream():
        raw.extend(chunk)
        if len(raw) > MAX_BRIDGE_BODY:
            raise HTTPException(status_code=413, detail=tr("Request too large"))
    return mt_bridge.parse_fields(bytes(raw)) if raw else {}


BridgeFields = Annotated[dict, Depends(bridge_fields)]


def _text(status_code: int, message: str) -> PlainTextResponse:
    return PlainTextResponse(message + "\n", status_code=status_code)


def _bridge_account(request: Request, db, fields: dict) -> TradingAccount | None:
    token = (
        request.headers.get(BRIDGE_TOKEN_HEADER) or request.query_params.get("token") or fields.get("token") or ""
    ).strip()
    return execution.account_for_token(db, _box(request), token) if token else None


def _unknown_token() -> PlainTextResponse:
    return _text(401, tr("Unknown or missing bridge token"))


@router.get("/api/bridge/orders", response_class=PlainTextResponse)
def bridge_poll(request: Request, db: DbDep, settings: SettingsDep):
    """Queued orders, one per line: ``id|symbol|side|type|lots|price|sl|tp`` (empty body = nothing to do)."""
    account = _bridge_account(request, db, {})
    if account is None:
        return _unknown_token()
    lines = execution.poll_orders(db, settings, account)
    return PlainTextResponse("".join(f"{line}\n" for line in lines))


@router.post("/api/bridge/orders/{execution_id}", response_class=PlainTextResponse)
def bridge_report(execution_id: int, request: Request, fields: BridgeFields, db: DbDep, settings: SettingsDep):
    """``status=filled|placed|rejected&ticket=..&price=..&message=..`` for one order."""
    account = _bridge_account(request, db, fields)
    if account is None:
        return _unknown_token()
    row = db.get(Execution, execution_id)
    if row is None or row.account_id != account.id or row.bridge != "mt_bridge":
        account.last_seen_at = execution.local_now(settings)
        db.commit()
        return _text(404, tr("Unknown order {id}", id=execution_id))
    try:
        execution.report_order(db, settings, account, row, fields)
    except execution.ExecutionError as exc:
        return _text(exc.status_code, str(exc))
    return _text(200, "OK")


@router.post("/api/bridge/account", response_class=PlainTextResponse)
def bridge_account(request: Request, fields: BridgeFields, db: DbDep, settings: SettingsDep):
    """``balance=..&equity=..&margin=..&free_margin=..&profit=..&positions=..``: the account's live figures."""
    account = _bridge_account(request, db, fields)
    if account is None:
        return _unknown_token()
    if account.connection != "mt_bridge":
        account.last_seen_at = execution.local_now(settings)
        db.commit()
        return _text(409, tr("This account is not set to the MetaTrader bridge"))
    try:
        execution.report_account(db, settings, account, fields)
    except execution.ExecutionError as exc:
        return _text(exc.status_code, str(exc))
    return _text(200, "OK")
