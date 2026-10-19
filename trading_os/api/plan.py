"""Trading plan: account settings, markets, sessions, strategies, confluences, checklist."""

from fastapi import APIRouter
from sqlalchemy import select

from .. import APP_NAME, __version__
from ..constants import ASSET_CLASSES, BIASES, CURRENCIES, EMOTIONS, TIMEFRAMES
from ..models import ChecklistItem, Confluence, Market, Strategy, TradingSession
from ..schemas import (
    ChecklistItemIn,
    ChecklistItemOut,
    ConfluenceIn,
    ConfluenceOut,
    MarketIn,
    MarketOut,
    SessionIn,
    SessionOut,
    SettingsIn,
    SettingsOut,
    StrategyIn,
    StrategyOut,
)
from ..services.account import local_now
from .crud import crud_router
from .deps import ConfigDep, DbDep, SettingsDep

router = APIRouter(prefix="/api", tags=["plan"])


@router.get("/settings", response_model=SettingsOut)
def read_settings(settings: SettingsDep):
    return settings


@router.put("/settings", response_model=SettingsOut)
def update_settings(payload: SettingsIn, settings: SettingsDep, db: DbDep):
    for key, value in payload.model_dump().items():
        setattr(settings, key, value)
    db.commit()
    return settings


@router.get("/plan")
def read_plan(settings: SettingsDep, db: DbDep, config: ConfigDep):
    """The whole trading plan in one request - the frontend's bootstrap call."""
    now = local_now(settings)

    def dump(model, schema, *order):
        return [schema.model_validate(obj).model_dump() for obj in db.scalars(select(model).order_by(*order))]

    return {
        "app": {"name": APP_NAME, "version": __version__, "data_dir": str(config.data_dir)},
        "now": now.isoformat(),
        "today": now.date().isoformat(),
        "settings": SettingsOut.model_validate(settings).model_dump(),
        "markets": dump(Market, MarketOut, Market.symbol),
        "sessions": dump(TradingSession, SessionOut, TradingSession.start_utc),
        "strategies": dump(Strategy, StrategyOut, Strategy.name),
        "confluences": dump(Confluence, ConfluenceOut, Confluence.name),
        "checklist_items": dump(ChecklistItem, ChecklistItemOut, ChecklistItem.sort_order, ChecklistItem.id),
        "enums": {
            "emotions": EMOTIONS,
            "timeframes": TIMEFRAMES,
            "asset_classes": ASSET_CLASSES,
            "currencies": CURRENCIES,
            "biases": BIASES,
        },
    }


plan_routers = [
    router,
    crud_router(Market, MarketIn, MarketOut, path="markets", label="market", order_by=(Market.symbol,)),
    crud_router(
        TradingSession, SessionIn, SessionOut, path="sessions", label="session", order_by=(TradingSession.start_utc,)
    ),
    crud_router(Strategy, StrategyIn, StrategyOut, path="strategies", label="strategy", order_by=(Strategy.name,)),
    crud_router(
        Confluence, ConfluenceIn, ConfluenceOut, path="confluences", label="confluence", order_by=(Confluence.name,)
    ),
    crud_router(
        ChecklistItem,
        ChecklistItemIn,
        ChecklistItemOut,
        path="checklist-items",
        label="checklist item",
        order_by=(ChecklistItem.sort_order, ChecklistItem.id),
    ),
]
