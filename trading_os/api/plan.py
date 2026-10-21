"""Trading plan: account settings, markets, sessions, strategies, confluences, checklist."""

from datetime import datetime
from zoneinfo import ZoneInfo

from fastapi import APIRouter, HTTPException
from pydantic import ValidationError
from sqlalchemy import func, select

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
from ..models import (
    AlertRule,
    BacktestTrade,
    ChecklistItem,
    Confluence,
    Market,
    MistakeType,
    NewsEvent,
    Strategy,
    TradingSession,
)
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
from ..services import i18n
from ..services.account import local_now
from .crud import crud_router
from .deps import ConfigDep, DbDep, SettingsDep

router = APIRouter(prefix="/api", tags=["plan"])


@router.get("/settings", response_model=SettingsOut)
def read_settings(settings: SettingsDep):
    return settings


@router.put("/settings", response_model=SettingsOut)
def update_settings(payload: SettingsIn, settings: SettingsDep, db: DbDep):
    # Only fields the client sent are changed, so partial forms can't reset others; the merged result
    # must still be valid as a whole (e.g. max risk >= default risk).
    changes = payload.model_dump(exclude_unset=True)
    merged = {name: getattr(settings, name) for name in SettingsIn.model_fields if hasattr(settings, name)}
    try:
        SettingsIn.model_validate({**merged, **changes})
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=exc.errors()[0]["msg"].removeprefix("Value error, ")) from exc
    if changes.get("timezone") and changes["timezone"] != settings.timezone:
        _move_absolute_times(db, settings, settings.timezone, changes["timezone"])
    for key, value in changes.items():
        setattr(settings, key, value)
    db.commit()
    return settings


def _move_absolute_times(db, settings, old_tz: str, new_tz: str) -> None:
    """Re-express real moments (the cool-off, scheduled news releases) in the new account time zone.

    Journal entry/exit times stay as recorded: they are the wall-clock times the trader saw.
    """

    def convert(value: datetime | None) -> datetime | None:
        if value is None:
            return None
        return value.replace(tzinfo=ZoneInfo(old_tz)).astimezone(ZoneInfo(new_tz)).replace(tzinfo=None)

    settings.cooloff_until = convert(settings.cooloff_until)
    settings.cooloff_started_at = convert(settings.cooloff_started_at)
    for event in db.scalars(select(NewsEvent)):
        event.time = convert(event.time)


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
            "languages": [{"code": code, "name": name} for code, name in i18n.LANGUAGES.items()],
        },
    }


plan_routers = [
    router,
    crud_router(Market, MarketIn, MarketOut, path="markets", label="market", order_by=(Market.symbol,)),
    crud_router(
        TradingSession,
        SessionIn,
        SessionOut,
        path="sessions",
        label="session",
        order_by=(TradingSession.start_utc,),
        on_update=lambda db, obj, before: session_updated(db, obj, before),
        on_delete=lambda db, obj, force: session_deleted(db, obj, force),
    ),
    crud_router(
        Strategy,
        StrategyIn,
        StrategyOut,
        path="strategies",
        label="strategy",
        order_by=(Strategy.name,),
        on_delete=lambda db, strategy, force: _guard_strategy_delete(db, strategy, force),
    ),
    crud_router(
        Confluence,
        ConfluenceIn,
        ConfluenceOut,
        path="confluences",
        label="confluence",
        order_by=(Confluence.name,),
        on_update=lambda db, obj, before: confluence_updated(db, obj, before),
        on_delete=lambda db, obj, force: confluence_deleted(db, obj, force),
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


# --------------------------------------------------------------------------- plan integrity


def _rename_in_lists(db, field: str, old: str, new: str | None) -> None:
    """Replace (or remove, when ``new`` is None) a name in every strategy's ``field`` list."""
    for strategy in db.scalars(select(Strategy)):
        names = list(getattr(strategy, field) or [])
        if old in names:
            updated = [new if name == old else name for name in names if new is not None or name != old]
            setattr(strategy, field, list(dict.fromkeys(updated)))


def _rename_alert_confluences(db, old: str, new: str) -> None:
    for rule in db.scalars(select(AlertRule)):
        if any(c.get("confluence") == old for c in rule.conditions or []):
            rule.conditions = [{**c, "confluence": new} if c.get("confluence") == old else c for c in rule.conditions]


def confluence_updated(db, confluence: Confluence, before: dict) -> None:
    """Strategies (and alert rules) follow a renamed confluence instead of requiring the old name."""
    if before.get("name") and before["name"] != confluence.name:
        _rename_in_lists(db, "required_confluences", before["name"], confluence.name)
        _rename_alert_confluences(db, before["name"], confluence.name)


def confluence_deleted(db, confluence: Confluence, _force: bool) -> None:
    _rename_in_lists(db, "required_confluences", confluence.name, None)


def session_updated(db, session: TradingSession, before: dict) -> None:
    if before.get("name") and before["name"] != session.name:
        _rename_in_lists(db, "allowed_sessions", before["name"], session.name)


def session_deleted(db, session: TradingSession, _force: bool) -> None:
    _rename_in_lists(db, "allowed_sessions", session.name, None)


def _guard_strategy_delete(db, strategy: Strategy, force: bool) -> None:
    """Deleting a strategy also deletes its backtests and alert rules: refuse unless asked explicitly."""
    backtests = db.scalar(select(func.count(BacktestTrade.id)).where(BacktestTrade.strategy_id == strategy.id))
    rules = db.scalar(select(func.count(AlertRule.id)).where(AlertRule.strategy_id == strategy.id))
    if (backtests or rules) and not force:
        parts = [f"{backtests} backtest trade(s)" if backtests else "", f"{rules} alert rule(s)" if rules else ""]
        raise HTTPException(
            status_code=409,
            detail=f"{strategy.name} has {' and '.join(p for p in parts if p)} that would be deleted with it. "
            "Retire the strategy instead to keep them, or confirm to delete everything.",
        )
