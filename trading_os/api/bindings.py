"""Strategy bindings (v1.7): link an incubator/live strategy to a trading account for guarded auto-execution.

Creating or editing a binding never sends an order. Arming ``auto_execute`` for a real account needs the typed
account name (paper accounts need no confirmation), and the account's own order sending must already be on. The
sending itself happens only from the alert scanner, through ``services.auto_execute`` and ``services.execution``.
"""

from typing import Annotated, Literal

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import Field, StringConstraints
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from ..models import Strategy, StrategyBinding, TradingAccount
from ..schemas import FiniteModel
from ..services import accounts, auto_execute
from ..services.i18n import tr
from .deps import DbDep, SettingsDep, get_or_404

router = APIRouter(prefix="/api/bindings", tags=["bindings"])


class BindingIn(FiniteModel):
    account_id: int
    risk_pct: float = Field(1.0, gt=auto_execute.MIN_RISK_PCT - 1e-9, le=auto_execute.MAX_RISK_PCT)
    max_lots_per_trade: float | None = Field(None, gt=0, le=1000)
    order_type: Literal["market", "limit"] = "market"
    max_trades_per_day: int = Field(3, ge=0, le=auto_execute.MAX_DAILY_CAP)
    require_checklist: bool = True
    notify: bool = True
    enabled: bool = True


class ArmIn(FiniteModel):
    auto_execute: bool
    confirm: Annotated[str, StringConstraints(max_length=100)] = ""


def _strategy(db, strategy_id: int) -> Strategy:
    return get_or_404(db, Strategy, strategy_id)


@router.get("")
def list_bindings(db: DbDep, settings: SettingsDep):
    rows = db.scalars(select(StrategyBinding).order_by(StrategyBinding.id))
    return [auto_execute.binding_out(db, settings, row) for row in rows]


@router.get("/strategy/{strategy_id}")
def for_strategy(strategy_id: int, db: DbDep, settings: SettingsDep):
    _strategy(db, strategy_id)
    binding = auto_execute.binding_for_strategy(db, strategy_id)
    return {"binding": auto_execute.binding_out(db, settings, binding) if binding else None}


@router.put("/strategy/{strategy_id}")
def upsert(strategy_id: int, payload: BindingIn, db: DbDep, settings: SettingsDep):
    """Create or update the binding for a strategy (one account per strategy). Never arms automation."""
    _strategy(db, strategy_id)
    account = get_or_404(db, TradingAccount, payload.account_id)
    binding = auto_execute.binding_for_strategy(db, strategy_id)
    moved = binding is not None and binding.account_id != account.id
    if binding is None:
        binding = StrategyBinding(strategy_id=strategy_id)
        db.add(binding)
    binding.account_id = account.id
    binding.risk_pct = payload.risk_pct
    binding.max_lots_per_trade = payload.max_lots_per_trade
    binding.order_type = payload.order_type
    binding.max_trades_per_day = payload.max_trades_per_day
    binding.require_checklist = payload.require_checklist
    binding.notify = payload.notify
    binding.enabled = payload.enabled
    if moved:
        binding.auto_execute = False  # a different account must be armed again on purpose
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail=tr("This strategy is already bound to that account")) from exc
    return auto_execute.binding_out(db, settings, binding)


@router.post("/{binding_id}/arm")
def arm(binding_id: int, payload: ArmIn, request: Request, db: DbDep, settings: SettingsDep):
    """Turn automatic execution on (typed account name for a real account) or off (always allowed)."""
    binding = get_or_404(db, StrategyBinding, binding_id)
    if not payload.auto_execute:
        binding.auto_execute = False
        db.commit()
        return auto_execute.binding_out(db, settings, binding)
    account = get_or_404(db, TradingAccount, binding.account_id)
    problem = auto_execute.arming_problem(settings, account)
    if problem:
        raise HTTPException(status_code=409, detail=problem)
    label = accounts.account_label(settings, account)
    if account.connection != "paper" and (payload.confirm or "").strip() != label:
        raise HTTPException(
            status_code=422,
            detail=tr(
                "Type the account name exactly ({name}) to let Trading OS place real orders by itself", name=label
            ),
        )
    binding.enabled = True
    binding.auto_execute = True
    db.commit()
    return auto_execute.binding_out(db, settings, binding)


@router.delete("/{binding_id}", status_code=204)
def delete(binding_id: int, db: DbDep):
    db.delete(get_or_404(db, StrategyBinding, binding_id))
    db.commit()
    return Response(status_code=204)
