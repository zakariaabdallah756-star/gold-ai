"""Strategy alert rules, scanner and alert feed.

Alerts are informational: nothing here creates or executes a trade. Provider
API keys and the push token are stored locally and never returned (only a
masked hint).
"""

import hmac
import re
import time
from datetime import datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import Field, StringConstraints, field_validator, model_validator
from sqlalchemy import delete, func, select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from ..models import AlertRule, Candle, Market, NotificationLog, Strategy, StrategyAlert, Trade
from ..schemas import FiniteModel
from ..services import alerts, detectors, market_data
from ..services.account import local_now
from ..services.trades import normalize_time
from .deps import DbDep, SettingsDep, get_or_404

router = APIRouter(prefix="/api/alerts", tags=["alerts"])

SYMBOL_RE = re.compile(r"^[A-Z0-9._-]{2,20}$")
PROVIDER_SYMBOL_RE = re.compile(r"^[A-Za-z0-9.^=/_:-]{1,30}$")
MAX_CONDITIONS = 12
MAX_PUSH_BARS = 2000
KEEP_PUSHED_BARS = 5000  # newest candles kept per symbol and timeframe
KEY_PROVIDERS = {"twelvedata": "twelvedata", "push": "push_token"}  # provider -> market_api_keys entry
INTERVAL_CHOICES = (60, 300, 900, 1800, 3600)
STOP_METHOD_INFO = (
    ("swing", "Swing", "Beyond the latest swing low (long) or high (short) within 20 candles."),
    ("signal", "Signal structure", "Beyond the structure the conditions found (swept wick, opposite swing, gap)."),
    ("atr", "ATR", "Entry minus / plus a multiple of ATR 14."),
)
PUSH_EXAMPLE = {
    "symbol": "XAUUSD",
    "timeframe": "M15",
    "token": "your-push-token",
    "bars": [{"time": "2026-10-20T09:45:00Z", "open": 2648.1, "high": 2652.4, "low": 2646.9, "close": 2651.7}],
}

Symbol = Annotated[str, StringConstraints(strip_whitespace=True, to_upper=True, max_length=20)]


def _check_symbol(value: str) -> str:
    if not SYMBOL_RE.match(value):
        raise ValueError(f"'{value}' is not a valid symbol (2-20 letters, digits, '.', '_' or '-')")
    return value


class ConditionIn(FiniteModel):
    type: Annotated[str, StringConstraints(max_length=40)]
    params: dict[str, Any] = Field(default_factory=dict)
    confluence: Annotated[str, StringConstraints(max_length=80, strip_whitespace=True)] = ""

    @model_validator(mode="after")
    def _params(self) -> "ConditionIn":
        self.params = detectors.validate_params(self.type, self.params)
        return self


class RuleIn(FiniteModel):
    name: Annotated[str, StringConstraints(min_length=1, max_length=80, strip_whitespace=True)]
    enabled: bool = True
    strategy_id: int | None = None
    symbols: list[Symbol] = Field(min_length=1, max_length=20)
    timeframe: Literal[market_data.TIMEFRAMES] = "M15"
    provider: Literal[market_data.PROVIDER_KEYS] = "yahoo"
    provider_symbols: dict[Symbol, Annotated[str, StringConstraints(strip_whitespace=True, max_length=30)]] = Field(
        default_factory=dict
    )
    direction: Literal[alerts.DIRECTION_FILTERS] = "both"
    conditions: list[ConditionIn] = Field(min_length=1, max_length=MAX_CONDITIONS)
    min_conditions: int = Field(0, ge=0, le=MAX_CONDITIONS)
    stop_method: Literal[alerts.STOP_METHODS] = "swing"
    stop_buffer_pips: float = Field(0.0, ge=0, le=10_000)
    atr_multiple: float = Field(1.5, gt=0, le=20)
    targets_rr: list[float] = Field(default_factory=list, max_length=4)
    risk_pct: float | None = Field(None, gt=0, le=10)
    cooldown_minutes: int = Field(60, ge=0, le=1440)
    notify: bool = True

    @field_validator("symbols")
    @classmethod
    def _symbols(cls, values: list[str]) -> list[str]:
        return list(dict.fromkeys(_check_symbol(value) for value in values))

    @field_validator("targets_rr")
    @classmethod
    def _targets(cls, values: list[float]) -> list[float]:
        if any(not 0 < value <= 50 for value in values):
            raise ValueError("Targets must be between 0 and 50 R")
        if any(b <= a for a, b in zip(values, values[1:], strict=False)):
            raise ValueError("Targets must be in ascending order (e.g. 1, 2, 3 R)")
        return values

    @model_validator(mode="after")
    def _consistent(self) -> "RuleIn":
        if self.min_conditions > len(self.conditions):
            raise ValueError("Minimum conditions cannot exceed the number of conditions")
        mapped = {}
        for symbol, ticker in self.provider_symbols.items():
            if not ticker:
                continue
            if symbol not in self.symbols:
                raise ValueError(f"Provider symbol given for {symbol}, which the rule does not watch")
            if self.provider == "push":  # must match what POST /candles stores (upper-cased symbols)
                ticker = ticker.upper()
                if not SYMBOL_RE.match(ticker):
                    raise ValueError(
                        f"'{ticker}' cannot be pushed - pushed symbols use 2-20 letters, digits, '.', '_' or '-'"
                    )
            elif not PROVIDER_SYMBOL_RE.match(ticker):
                raise ValueError(f"'{ticker}' is not a valid provider symbol")
            mapped[symbol] = ticker
        self.provider_symbols = mapped
        return self


class ScannerIn(FiniteModel):
    enabled: bool
    interval_sec: int = Field(ge=alerts.MIN_INTERVAL, le=alerts.MAX_INTERVAL)


class AlertStatusIn(FiniteModel):
    status: Literal[alerts.ALERT_STATUSES]
    trade_id: int | None = None


class BarIn(FiniteModel):
    time: datetime
    open: float = Field(gt=0, allow_inf_nan=False)
    high: float = Field(gt=0, allow_inf_nan=False)
    low: float = Field(gt=0, allow_inf_nan=False)
    close: float = Field(gt=0, allow_inf_nan=False)
    volume: float | None = Field(None, ge=0, allow_inf_nan=False)

    @model_validator(mode="after")
    def _ohlc(self) -> "BarIn":
        if self.high < max(self.open, self.close, self.low) or self.low > min(self.open, self.close):
            raise ValueError(f"Bar {self.time.isoformat()}: high must be the highest and low the lowest price")
        return self


class CandlesIn(FiniteModel):
    symbol: Symbol
    timeframe: Literal[market_data.TIMEFRAMES]
    bars: list[BarIn] = Field(min_length=1, max_length=MAX_PUSH_BARS)
    token: Annotated[str, StringConstraints(max_length=200)] | None = None

    @field_validator("symbol")
    @classmethod
    def _symbol(cls, value: str) -> str:
        return _check_symbol(value)


class ProviderKeyIn(FiniteModel):
    api_key: Annotated[str, StringConstraints(strip_whitespace=True, max_length=200)] = ""


# --------------------------------------------------------------------------- serialisation


def _rule_out(rule: AlertRule, strategy: Strategy | None) -> dict:
    return {
        "id": rule.id,
        "name": rule.name,
        "enabled": rule.enabled,
        "strategy_id": rule.strategy_id,
        "strategy": {"id": strategy.id, "name": strategy.name, "status": strategy.status} if strategy else None,
        "symbols": list(rule.symbols or []),
        "timeframe": rule.timeframe,
        "provider": rule.provider,
        "provider_label": market_data.PROVIDERS[rule.provider].label if rule.provider in market_data.PROVIDERS else "",
        "provider_symbols": dict(rule.provider_symbols or {}),
        "direction": rule.direction,
        "conditions": list(rule.conditions or []),
        "min_conditions": rule.min_conditions,
        "stop_method": rule.stop_method,
        "stop_buffer_pips": rule.stop_buffer_pips,
        "atr_multiple": rule.atr_multiple,
        "targets_rr": list(rule.targets_rr or []),
        "risk_pct": rule.risk_pct,
        "cooldown_minutes": rule.cooldown_minutes,
        "notify": rule.notify,
        "last_checked_at": rule.last_checked_at,
        "last_candle_at": rule.last_candle_at,
        "last_alert_at": rule.last_alert_at,
        "last_error": rule.last_error,
    }


def _alert_out(alert: StrategyAlert, notification_status: str | None = None) -> dict:
    details = alert.details or {}
    precheck = details.get("precheck") or []
    return {
        "id": alert.id,
        "rule_id": alert.rule_id,
        "created_at": alert.created_at,
        "candle_time": alert.candle_time,
        "strategy": alert.strategy,
        "symbol": alert.symbol,
        "timeframe": alert.timeframe,
        "direction": alert.direction,
        "entry_price": alert.entry_price,
        "stop_loss": alert.stop_loss,
        "take_profits": list(alert.take_profits or []),
        "planned_rr": alert.planned_rr,
        "lots": alert.lots,
        "risk_amount": alert.risk_amount,
        "confluences": list(alert.confluences or []),
        "details": details,
        "in_test": bool(details.get("in_test")),
        "precheck_passes": not any(v.get("severity") == "block" for v in precheck),
        "signal_text": alert.signal_text,
        "status": alert.status,
        "trade_id": alert.trade_id,
        "notification_id": alert.notification_id,
        "notification_status": notification_status,  # sent | partial | failed | skipped | held | None
    }


def _notification_statuses(db, alerts_: list[StrategyAlert]) -> dict[int, str]:
    ids = [a.notification_id for a in alerts_ if a.notification_id]
    if not ids:
        return {}
    return dict(db.execute(select(NotificationLog.id, NotificationLog.status).where(NotificationLog.id.in_(ids))).all())


def _strategy_or_422(db, strategy_id: int | None) -> Strategy | None:
    if strategy_id is None:
        return None
    strategy = db.get(Strategy, strategy_id)
    if strategy is None:
        raise HTTPException(status_code=422, detail=f"Strategy {strategy_id} not found")
    return strategy


def _apply(rule: AlertRule, data: RuleIn) -> None:
    for key, value in data.model_dump().items():
        setattr(rule, key, value)


# --------------------------------------------------------------------------- meta & providers


@router.get("/meta")
def meta(settings: SettingsDep, db: DbDep):
    keys = settings.market_api_keys or {}
    symbols = sorted(set(db.scalars(select(Market.symbol))) | set(market_data.YAHOO_SYMBOLS))
    providers = []
    for provider in market_data.PROVIDERS.values():
        secret = keys.get(KEY_PROVIDERS.get(provider.key, ""), "")
        providers.append(
            {
                "key": provider.key,
                "label": provider.label,
                "description": provider.description,
                "needs_key": provider.needs_key,
                "key_configured": bool(secret),
                "key_hint": market_data.mask_secret(secret),
                "symbol_map": {symbol: market_data.provider_symbol(provider.key, symbol) for symbol in symbols},
            }
        )
    return {
        "condition_types": [spec.to_dict() for spec in detectors.CONDITION_TYPES.values()],
        "providers": providers,
        "timeframes": list(market_data.TIMEFRAMES),
        "stop_methods": [{"key": key, "label": label, "description": text} for key, label, text in STOP_METHOD_INFO],
        "directions": list(alerts.DIRECTION_FILTERS),
        "statuses": list(alerts.ALERT_STATUSES),
        "interval_choices": list(INTERVAL_CHOICES),
        "max_conditions": MAX_CONDITIONS,
        "push": {
            "endpoint": "/api/alerts/candles",
            "token_required": bool(keys.get("push_token")),
            "max_bars": MAX_PUSH_BARS,
            "example": PUSH_EXAMPLE,
        },
    }


@router.put("/providers/{key}/key")
def set_provider_key(key: str, data: ProviderKeyIn, settings: SettingsDep, db: DbDep):
    """Store (or, with an empty value, remove) a provider API key or the push token. Never returned."""
    entry = KEY_PROVIDERS.get(key)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"Provider '{key}' does not take a key")
    keys = dict(settings.market_api_keys or {})
    if data.api_key:
        keys[entry] = data.api_key
    else:
        keys.pop(entry, None)
    settings.market_api_keys = keys
    db.commit()
    return {"provider": key, "configured": bool(data.api_key), "hint": market_data.mask_secret(data.api_key)}


# --------------------------------------------------------------------------- rules


@router.get("/rules")
def list_rules(db: DbDep):
    strategies = {s.id: s for s in db.scalars(select(Strategy))}
    rules = db.scalars(select(AlertRule).order_by(AlertRule.id))
    return [_rule_out(rule, strategies.get(rule.strategy_id)) for rule in rules]


@router.post("/rules", status_code=201)
def create_rule(data: RuleIn, db: DbDep):
    strategy = _strategy_or_422(db, data.strategy_id)
    rule = AlertRule()
    _apply(rule, data)
    db.add(rule)
    db.commit()
    return _rule_out(rule, strategy)


@router.get("/rules/{rule_id}")
def get_rule(rule_id: int, db: DbDep):
    rule = get_or_404(db, AlertRule, rule_id)
    return _rule_out(rule, db.get(Strategy, rule.strategy_id) if rule.strategy_id else None)


@router.put("/rules/{rule_id}")
def update_rule(rule_id: int, data: RuleIn, db: DbDep):
    rule = get_or_404(db, AlertRule, rule_id)
    strategy = _strategy_or_422(db, data.strategy_id)
    _apply(rule, data)
    rule.last_error = ""
    # Start afresh on the newest candle (no catch-up on candles judged under the old settings); alerted
    # candles stay remembered so none is sent twice.
    rule.scan_state = {
        symbol: {key: value for key, value in memory.items() if key != "through"}
        for symbol, memory in (rule.scan_state or {}).items()
        if isinstance(memory, dict)
    }
    db.commit()
    return _rule_out(rule, strategy)


@router.delete("/rules/{rule_id}", status_code=204)
def delete_rule(rule_id: int, db: DbDep):
    db.delete(get_or_404(db, AlertRule, rule_id))
    db.commit()
    return Response(status_code=204)


@router.post("/rules/{rule_id}/test")
def test_rule(rule_id: int, settings: SettingsDep, db: DbDep):
    """Dry run on the latest closed candles of each symbol: diagnostics only, nothing saved or sent."""
    rule = get_or_404(db, AlertRule, rule_id)
    now = local_now(settings)
    return {
        "rule_id": rule.id,
        "ran_at": now,
        "provider": rule.provider,
        "provider_label": market_data.PROVIDERS[rule.provider].label,
        "results": alerts.dry_run(db, settings, rule, now),
    }


# --------------------------------------------------------------------------- scanner


def _scanner_out(request: Request, settings, db) -> dict:
    scheduler = request.app.state.scheduler
    job = scheduler.get(alerts.JOB_NAME)
    last = job.last_result if isinstance(job.last_result, dict) else None
    return {
        "enabled": settings.scanner_enabled,
        "interval_sec": alerts.clamp_interval(settings.scanner_interval_sec),
        "thread_running": scheduler.running,
        "last_run_at": last["ran_at"] if last else None,
        "last_manual": last["manual"] if last else None,
        "last_error": job.last_error,
        "last_results": last["results"] if last else [],
        "rules_enabled": db.scalar(select(func.count(AlertRule.id)).where(AlertRule.enabled.is_(True))) or 0,
        "gap_free_interval_sec": _gap_free_interval(db),
    }


def _gap_free_interval(db) -> int | None:
    """Longest scan interval at which no enabled rule skips a candle (catch-up covers what closed meanwhile)."""
    timeframes = set(db.scalars(select(AlertRule.timeframe).where(AlertRule.enabled.is_(True))))
    limits = [
        min(alerts.MAX_CATCH_UP * market_data.timeframe_delta(tf), alerts.stale_after(tf)).total_seconds()
        for tf in timeframes
        if tf in market_data.TF_MINUTES
    ]
    return int(min(limits)) if limits else None


@router.get("/scanner")
def scanner_status(request: Request, settings: SettingsDep, db: DbDep):
    return _scanner_out(request, settings, db)


@router.put("/scanner")
def update_scanner(data: ScannerIn, request: Request, settings: SettingsDep, db: DbDep):
    turned_on = data.enabled and not settings.scanner_enabled
    settings.scanner_enabled = data.enabled
    settings.scanner_interval_sec = data.interval_sec
    db.commit()
    job = request.app.state.scheduler.get(alerts.JOB_NAME)
    soon = time.monotonic() + (1.0 if turned_on else data.interval_sec)
    job.next_run = min(job.next_run, soon)
    return _scanner_out(request, settings, db)


@router.post("/scan")
def scan_now(request: Request):
    """Scan every enabled rule now (also when the background scanner is off)."""
    job = alerts.scan_now(request.app.state.scheduler)
    if job.last_error or not isinstance(job.last_result, dict):
        raise HTTPException(status_code=500, detail=f"The scan failed: {job.last_error or 'no result'}")
    return job.last_result


# --------------------------------------------------------------------------- pushed candles


@router.post("/candles")
def push_candles(data: CandlesIn, settings: SettingsDep, db: DbDep):
    """Receive closed candles from the user's platform for the ``push`` provider (upsert by bar open time).

    Bars that have not closed yet (open time + timeframe after now) are skipped: the scanner must never
    judge a forming candle, and the closed version arrives with a later push.
    """
    expected = (settings.market_api_keys or {}).get("push_token", "")
    if expected and not hmac.compare_digest((data.token or "").encode(), expected.encode()):
        raise HTTPException(status_code=401, detail="Invalid or missing push token")
    now = local_now(settings)
    delta = market_data.timeframe_delta(data.timeframe)
    rows = {}
    forming = 0
    for bar in data.bars:
        moment = normalize_time(bar.time, settings.timezone)
        if moment + delta > now:
            forming += 1
            continue
        rows[moment] = {
            "symbol": data.symbol,
            "timeframe": data.timeframe,
            "time": moment,
            "open": bar.open,
            "high": bar.high,
            "low": bar.low,
            "close": bar.close,
            "volume": bar.volume,
        }
    summary = {
        "symbol": data.symbol,
        "timeframe": data.timeframe,
        "received": len(data.bars),
        "skipped_forming": forming,
    }
    if not rows:
        return {
            **summary,
            "inserted": 0,
            "updated": 0,
            "first": None,
            "last": None,
            "note": "Every bar was still forming (or is in the future - check the time zone of the bar times).",
        }
    existing = set(
        db.scalars(
            select(Candle.time).where(
                Candle.symbol == data.symbol,
                Candle.timeframe == data.timeframe,
                Candle.time >= min(rows),
                Candle.time <= max(rows),
            )
        )
    )
    statement = sqlite_insert(Candle).values(list(rows.values()))
    statement = statement.on_conflict_do_update(
        index_elements=["symbol", "timeframe", "time"],
        set_={column: statement.excluded[column] for column in ("open", "high", "low", "close", "volume")},
    )
    db.execute(statement)
    cutoff = db.scalar(
        select(Candle.time)
        .where(Candle.symbol == data.symbol, Candle.timeframe == data.timeframe)
        .order_by(Candle.time.desc())
        .offset(KEEP_PUSHED_BARS)
        .limit(1)
    )
    if cutoff is not None:
        db.execute(
            delete(Candle).where(
                Candle.symbol == data.symbol, Candle.timeframe == data.timeframe, Candle.time <= cutoff
            )
        )
    db.commit()
    inserted = len(set(rows) - existing)
    return {
        **summary,
        "inserted": inserted,
        "updated": len(rows) - inserted,
        "first": min(rows),
        "last": max(rows),
    }


# --------------------------------------------------------------------------- alert feed


@router.get("")
def list_alerts(db: DbDep, status: Literal[alerts.ALERT_STATUSES] | None = None, limit: int = 100):
    limit = max(1, min(limit, 500))
    query = select(StrategyAlert).order_by(StrategyAlert.created_at.desc(), StrategyAlert.id.desc()).limit(limit)
    if status:
        query = query.where(StrategyAlert.status == status)
    counts = dict.fromkeys(alerts.ALERT_STATUSES, 0)
    for key, count in db.execute(select(StrategyAlert.status, func.count()).group_by(StrategyAlert.status)):
        counts[key] = count
    rows = list(db.scalars(query))
    statuses = _notification_statuses(db, rows)
    return {
        "alerts": [_alert_out(a, statuses.get(a.notification_id)) for a in rows],
        "counts": {**counts, "all": sum(counts.values())},
    }


@router.get("/{alert_id}")
def get_alert(alert_id: int, db: DbDep):
    alert = get_or_404(db, StrategyAlert, alert_id)
    return _alert_out(alert, _notification_statuses(db, [alert]).get(alert.notification_id))


@router.post("/{alert_id}/status")
def set_alert_status(alert_id: int, data: AlertStatusIn, db: DbDep):
    alert = get_or_404(db, StrategyAlert, alert_id)
    if data.trade_id is not None:
        if data.status != "taken":
            raise HTTPException(status_code=422, detail="A trade can only be linked to a taken alert")
        get_or_404(db, Trade, data.trade_id)
        alert.trade_id = data.trade_id
    alert.status = data.status
    db.commit()
    return _alert_out(alert)


@router.delete("/{alert_id}", status_code=204)
def delete_alert(alert_id: int, db: DbDep):
    db.delete(get_or_404(db, StrategyAlert, alert_id))
    db.commit()
    return Response(status_code=204)
