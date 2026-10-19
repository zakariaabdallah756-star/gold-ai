"""Shared FastAPI dependencies."""

from typing import Annotated

from fastapi import Depends, HTTPException, Request
from sqlalchemy.orm import Session

from ..config import AppConfig
from ..database import get_db
from ..models import AccountSettings
from ..services import account

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
        raise HTTPException(status_code=404, detail=f"{model.__name__} {item_id} not found")
    return obj
