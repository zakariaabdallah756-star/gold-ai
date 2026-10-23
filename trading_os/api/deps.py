"""Shared FastAPI dependencies."""

from typing import Annotated

from fastapi import Depends, HTTPException, Query, Request
from sqlalchemy.orm import Session

from ..config import AppConfig
from ..database import get_db
from ..models import AccountSettings, TradingAccount
from ..services import account, accounts
from ..services.i18n import tr

DbDep = Annotated[Session, Depends(get_db)]


def get_config(request: Request) -> AppConfig:
    return request.app.state.config


def get_account_settings(db: DbDep) -> AccountSettings:
    return account.get_settings(db)


ConfigDep = Annotated[AppConfig, Depends(get_config)]
SettingsDep = Annotated[AccountSettings, Depends(get_account_settings)]


def get_or_404(db: Session, model, item_id):
    obj = db.get(model, item_id)
    if obj is None:
        raise HTTPException(
            status_code=404, detail=tr("{model} {item_id} not found", model=model.__name__, item_id=item_id)
        )
    return obj


def get_scope(
    db: DbDep,
    settings: SettingsDep,
    account: str | None = Query(None, description="Trading account: active (default), all, or an account id"),
) -> accounts.Scope:
    """The trades a request counts: the account the app shows unless ``account`` asks for another."""
    if account in (None, "", "active"):
        return accounts.resolve_scope(db, settings)
    if account == "all":
        return accounts.resolve_scope(db, settings, None)
    try:
        account_id = int(account)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=tr("Unknown trading account {id}", id=account)) from exc
    if db.get(TradingAccount, account_id) is None:
        raise HTTPException(status_code=404, detail=tr("Unknown trading account {id}", id=account_id))
    return accounts.resolve_scope(db, settings, account_id)


ScopeDep = Annotated[accounts.Scope, Depends(get_scope)]


def scope_currency(settings: AccountSettings, scope: accounts.Scope) -> str:
    """The currency of the money a scoped report shows."""
    if scope.account is not None and not accounts.is_default(settings, scope.account):
        return scope.account.currency
    return settings.currency
