"""Trading accounts: register, edit, archive, switch the account the app shows, live account metrics.

Secrets are write-only (``PUT /api/accounts/{id}/credentials``): no endpoint returns them, only which ones
are set with a short hint. Order routing (connection, execution) lives in ``api/execution.py``.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, Request, Response
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from ..models import Trade, TradingAccount
from ..schemas import ActiveAccountIn, CredentialsIn, TradingAccountIn
from ..services import account as account_service
from ..services import accounts, secrets_store
from ..services.execution import cancel_queued  # switching sending off only: nothing here sends orders
from ..services.i18n import tr
from .deps import DbDep, SettingsDep, get_or_404

router = APIRouter(prefix="/api/accounts", tags=["accounts"])


def secret_box(request: Request) -> secrets_store.SecretBox:
    return request.app.state.secrets


def _enums() -> dict:
    return {
        "kinds": list(accounts.KINDS),
        "platforms": list(accounts.PLATFORMS),
        "connections": list(accounts.CONNECTIONS),
        "statuses": list(accounts.STATUSES),
    }


def _commit(db) -> None:
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail=tr("An account with that name already exists")) from exc


@router.get("")
def list_accounts(db: DbDep, settings: SettingsDep):
    now = account_service.local_now(settings)
    rows = db.scalars(select(TradingAccount).order_by(TradingAccount.id))
    return {
        "accounts": [
            accounts.account_out(db, settings, a, accounts.account_metrics(db, settings, a, now)) for a in rows
        ],
        "active_account_id": settings.active_account_id,
        "default_account_id": settings.default_account_id,
        "enums": _enums(),
    }


@router.get("/summary")
def account_summary(db: DbDep, settings: SettingsDep, account: str = Query("active")):
    """Balance, equity, floating P&L and margin of the account shown (``account``: active | all | an id)."""
    if account == "active":
        scope = accounts.resolve_scope(db, settings)
    elif account == "all":
        scope = accounts.resolve_scope(db, settings, None)
    else:
        try:
            account_id = int(account)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=tr("Unknown trading account {id}", id=account)) from exc
        get_or_404(db, TradingAccount, account_id)
        scope = accounts.resolve_scope(db, settings, account_id)
    return accounts.summary(db, settings, scope, account_service.local_now(settings))


@router.put("/active")
def set_active(payload: ActiveAccountIn, db: DbDep, settings: SettingsDep):
    """Show one account (or, with ``account_id`` null, every account together) across the app."""
    if payload.account_id is not None:
        get_or_404(db, TradingAccount, payload.account_id)
    settings.active_account_id = payload.account_id
    db.commit()
    return {"active_account_id": settings.active_account_id, "status": account_service.daily_status(db, settings)}


@router.post("", status_code=201)
def create_account(payload: TradingAccountIn, db: DbDep, settings: SettingsDep):
    account = TradingAccount(**payload.model_dump())
    db.add(account)
    _commit(db)
    return accounts.account_out(db, settings, account)


@router.put("/{account_id}")
def update_account(account_id: int, payload: TradingAccountIn, db: DbDep, settings: SettingsDep):
    account = get_or_404(db, TradingAccount, account_id)
    if accounts.is_default(settings, account) and payload.status != "active":
        raise HTTPException(status_code=409, detail=tr("The default account cannot be archived"))
    before = (account.login, account.server)
    for key, value in payload.model_dump().items():
        setattr(account, key, value)
    # Where orders go changed (another MT5 login/server) or the account was archived: sending switches off.
    moved = account.connection == "mt5_terminal" and before != (account.login, account.server)
    if account.execution_enabled and (moved or account.status != "active"):
        account.execution_enabled = False
        cancel_queued(db, settings, account, tr("Cancelled: order sending was switched off"))
    if accounts.is_default(settings, account):
        accounts.sync_settings_from_default(settings, account)
    if payload.status != "active" and settings.active_account_id == account.id:
        settings.active_account_id = None
    _commit(db)
    return accounts.account_out(db, settings, account)


@router.delete("/{account_id}", status_code=204)
def delete_account(account_id: int, db: DbDep, settings: SettingsDep):
    account = get_or_404(db, TradingAccount, account_id)
    if accounts.is_default(settings, account):
        raise HTTPException(status_code=409, detail=tr("The default account cannot be deleted"))
    trades = db.scalar(select(func.count(Trade.id)).where(Trade.account_id == account.id)) or 0
    if trades:
        raise HTTPException(
            status_code=409,
            detail=tr(
                "{count} trades are logged on this account: archive it instead (journal history is never deleted)",
                count=trades,
            ),
        )
    if settings.active_account_id == account.id:
        settings.active_account_id = None
    secrets_store.remove_account(db, account.id)
    db.delete(account)
    db.commit()
    return Response(status_code=204)


@router.put("/{account_id}/credentials")
def set_credentials(account_id: int, payload: CredentialsIn, request: Request, db: DbDep):
    """Store, replace or remove (with "") the account's secrets. They are never returned."""
    account = get_or_404(db, TradingAccount, account_id)
    box = secret_box(request)
    for kind, value in payload.model_dump(exclude_none=True).items():
        secrets_store.put(db, box, account.id, kind, value.strip())
    db.commit()
    return {"credentials": secrets_store.described(db, account.id)}
