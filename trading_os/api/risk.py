"""Risk management: position sizing calculator and daily discipline status."""

from fastapi import APIRouter

from ..models import Market
from ..schemas import DailyStatusOut, PositionSizeIn, PositionSizeOut
from ..services import account, accounts
from ..services.guardrails import daily_violations, trade_rule_violations
from ..services.sizing import calculate_position_size
from .deps import DbDep, SettingsDep, get_or_404

router = APIRouter(prefix="/api/risk", tags=["risk"])


@router.get("/status", response_model=DailyStatusOut)
def read_status(settings: SettingsDep, db: DbDep):
    return account.daily_status(db, settings)


@router.post("/position-size", response_model=PositionSizeOut)
def position_size(payload: PositionSizeIn, settings: SettingsDep, db: DbDep):
    market = get_or_404(db, Market, payload.market_id)
    # Sized for the account a new trade would be logged on (the one shown, else the default).
    target = accounts.account_for_new_trade(db, settings, None)
    ids = (target.id,) if target is not None else None
    currency = accounts.currency_of(settings, target)
    equity = payload.account_equity or account.current_equity(db, settings, ids)
    risk_pct = payload.risk_pct or settings.default_risk_pct

    result = calculate_position_size(
        market,
        currency,
        equity,
        risk_pct,
        entry=payload.entry_price,
        stop=payload.stop_price,
        stop_pips=payload.stop_pips,
        take_profit=payload.take_profit,
        target_pips=payload.target_pips,
        fx_rate=payload.fx_rate,
    )

    violations = trade_rule_violations(
        settings,
        planned_rr=result.rr,
        has_take_profit=result.rr is not None,
        risk_amount=result.max_loss,
        equity=equity,
        confluence_count=0,
    )
    day = account.compute_day_state(db, settings, account_ids=ids)
    violations += daily_violations(settings, day, result.max_loss)

    return PositionSizeOut(
        **vars(result),
        violations=[violation.to_dict() for violation in violations],
    )
