"""Risk management: position sizing calculator and daily discipline status."""

from fastapi import APIRouter

from ..models import Market
from ..schemas import DailyStatusOut, PositionSizeIn, PositionSizeOut
from ..services import account
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
    equity = payload.account_equity or account.current_equity(db, settings)
    risk_pct = payload.risk_pct or settings.default_risk_pct

    result = calculate_position_size(
        market,
        settings.currency,
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
    day = account.compute_day_state(db, settings)
    violations += daily_violations(settings, day, result.max_loss)

    return PositionSizeOut(
        **vars(result),
        violations=[violation.to_dict() for violation in violations],
    )
