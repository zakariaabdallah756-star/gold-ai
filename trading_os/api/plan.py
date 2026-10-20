"""Trading plan: account settings, markets, sessions, strategies, confluences, checklist."""

from fastapi import APIRouter
from sqlalchemy import select

from .. import APP_NAME, __version__
from ..constants import (
    ASSET_CLASSES,
    BIASES,
    CURRENCIES,
    DEFAULT_TP_SPLITS,
    EMOTIONS,
    MAX_TARGETS,
    MOODS,
    NEWS_IMPACTS,
    NEWS_MODES,
    NEWS_PRESETS,
    TIMEFRAMES,
    TRAIL_TRIGGERS,
)
from ..models import ChecklistItem, Confluence, Market, MistakeType, Strategy, TradingSession
from ..schemas import (
    ChecklistItemIn,
    ChecklistItemOut,
    ConfluenceIn,
    ConfluenceOut,
    MarketIn,
    MarketOut,
    MistakeTypeIn,
    MistakeTypeOut,
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
    # Only fields the client sent are changed, so partial forms can't reset others.
    for key, value in payload.model_dump(exclude_unset=True).items():
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
        "mistake_types": dump(MistakeType, MistakeTypeOut, MistakeType.name),
        "enums": {
            "emotions": EMOTIONS,
            "timeframes": TIMEFRAMES,
            "asset_classes": ASSET_CLASSES,
            "currencies": CURRENCIES,
            "biases": BIASES,
            "moods": MOODS,
            "news_modes": NEWS_MODES,
            "news_impacts": NEWS_IMPACTS,
            "news_presets": [{"title": title, "currency": currency} for title, currency in NEWS_PRESETS],
            "trail_triggers": TRAIL_TRIGGERS,
            "max_targets": MAX_TARGETS,
            "tp_splits": {str(count): list(split) for count, split in DEFAULT_TP_SPLITS.items()},
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
    crud_router(
        MistakeType, MistakeTypeIn, MistakeTypeOut, path="mistakes", label="mistake", order_by=(MistakeType.name,)
    ),
]
