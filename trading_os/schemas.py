"""Pydantic request/response schemas.

Input schemas validate trade geometry up front (a long's stop must be below
entry, a short's target below entry, ...) so the statistics are never computed
on impossible data.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Annotated, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, StringConstraints, field_validator, model_validator


def _upper(value):
    # Runs before the pattern check, so "usd" or " xauusd " are accepted.
    return value.strip().upper() if isinstance(value, str) else value


HHMM = Annotated[str, StringConstraints(pattern=r"^([01]\d|2[0-3]):[0-5]\d$")]
Currency = Annotated[str, StringConstraints(pattern=r"^[A-Z]{3}$"), BeforeValidator(_upper)]
Symbol = Annotated[str, StringConstraints(pattern=r"^[A-Z0-9._/\-]{1,20}$"), BeforeValidator(_upper)]
ShortText = Annotated[str, StringConstraints(max_length=200, strip_whitespace=True)]
LongText = Annotated[str, StringConstraints(max_length=20_000)]
Name = Annotated[str, StringConstraints(min_length=1, max_length=80, strip_whitespace=True)]
Severity = Literal["block", "warn"]


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


# --------------------------------------------------------------------------- settings


class SettingsIn(BaseModel):
    account_name: Name = "Main Account"
    currency: Currency = "USD"
    starting_balance: float = Field(10_000.0, gt=0)
    timezone: str = "UTC"
    default_risk_pct: float = Field(1.0, gt=0, le=100)
    max_risk_pct: float = Field(2.0, gt=0, le=100)
    min_rr: float = Field(2.0, ge=0, le=50)
    trade_rule_mode: Literal["block", "warn"] = "block"
    max_daily_loss_pct: float = Field(3.0, gt=0, le=100)
    max_trades_per_day: int = Field(0, ge=0, le=100)
    daily_limit_mode: Literal["lock", "warn"] = "lock"
    require_checklist: bool = True
    warn_outside_sessions: bool = True

    @field_validator("timezone")
    @classmethod
    def _valid_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError(f"Unknown time zone '{value}'") from exc
        return value

    @model_validator(mode="after")
    def _risk_caps(self) -> SettingsIn:
        if self.max_risk_pct < self.default_risk_pct:
            raise ValueError("Max risk per trade must be >= default risk per trade")
        return self


class SettingsOut(SettingsIn, ORMModel):
    pass


# --------------------------------------------------------------------------- trading plan


class MarketIn(BaseModel):
    symbol: Symbol
    name: ShortText = ""
    asset_class: Literal["forex", "metal", "index", "commodity", "crypto", "stock"] = "forex"
    base_currency: Currency | None = None
    quote_currency: Currency = "USD"
    contract_size: float = Field(gt=0)
    pip_size: float = Field(gt=0)
    min_lot: float = Field(0.01, gt=0)
    lot_step: float = Field(0.01, gt=0)
    max_lot: float = Field(100.0, gt=0)
    active: bool = True
    notes: LongText = ""

    @field_validator("base_currency", mode="before")
    @classmethod
    def _blank_is_none(cls, value):
        return value or None

    @model_validator(mode="after")
    def _lot_bounds(self) -> MarketIn:
        if self.max_lot < self.min_lot:
            raise ValueError("Max lot must be >= min lot")
        return self


class MarketOut(MarketIn, ORMModel):
    id: int


class SessionIn(BaseModel):
    name: Annotated[str, StringConstraints(min_length=1, max_length=40, strip_whitespace=True)]
    start_utc: HHMM
    end_utc: HHMM
    active: bool = True

    @model_validator(mode="after")
    def _non_empty_window(self) -> SessionIn:
        if self.start_utc == self.end_utc:
            raise ValueError("Session start and end must differ")
        return self


class SessionOut(SessionIn, ORMModel):
    id: int


class StrategyIn(BaseModel):
    name: Annotated[str, StringConstraints(min_length=1, max_length=60, strip_whitespace=True)]
    description: LongText = ""
    timeframes: ShortText = ""
    rules: LongText = ""
    min_confluences: int = Field(0, ge=0, le=20)
    active: bool = True


class StrategyOut(StrategyIn, ORMModel):
    id: int


class ConfluenceIn(BaseModel):
    name: Name
    description: LongText = ""
    active: bool = True


class ConfluenceOut(ConfluenceIn, ORMModel):
    id: int


class ChecklistItemIn(BaseModel):
    label: Annotated[str, StringConstraints(min_length=1, max_length=160, strip_whitespace=True)]
    category: Annotated[str, StringConstraints(max_length=40, strip_whitespace=True)] = "General"
    required: bool = True
    sort_order: int = 0
    active: bool = True


class ChecklistItemOut(ChecklistItemIn, ORMModel):
    id: int


# --------------------------------------------------------------------------- checklist


class ChecklistDayIn(BaseModel):
    checked_item_ids: list[int] = Field(default_factory=list, max_length=500)
    bias: Literal["", "bullish", "bearish", "neutral"] = ""
    key_levels: LongText = ""
    news: LongText = ""
    notes: LongText = ""


class ChecklistDayOut(ChecklistDayIn):
    day: date
    completed: bool
    required_total: int
    required_done: int
    total: int
    done: int
    completed_at: datetime | None = None


# --------------------------------------------------------------------------- risk


class Violation(BaseModel):
    code: str
    message: str
    severity: Severity


class PositionSizeIn(BaseModel):
    market_id: int
    account_equity: float | None = Field(None, gt=0)
    risk_pct: float | None = Field(None, gt=0, le=100)
    entry_price: float | None = Field(None, gt=0)
    stop_price: float | None = Field(None, gt=0)
    stop_pips: float | None = Field(None, gt=0)
    take_profit: float | None = Field(None, gt=0)
    target_pips: float | None = Field(None, gt=0)
    fx_rate: float | None = Field(None, gt=0)

    @model_validator(mode="after")
    def _stop_defined(self) -> PositionSizeIn:
        has_prices = self.entry_price is not None and self.stop_price is not None
        if not has_prices and self.stop_pips is None:
            raise ValueError("Provide either entry + stop prices or a stop size in pips/points")
        if has_prices and self.entry_price == self.stop_price:
            raise ValueError("Stop price must differ from entry price")
        if self.take_profit is not None and self.entry_price is None:
            raise ValueError("A take-profit price needs an entry price")
        return self


class PositionSizeOut(BaseModel):
    symbol: str
    currency: str
    direction: Literal["long", "short"] | None
    account_equity: float
    risk_pct: float
    risk_amount: float
    stop_pips: float
    stop_distance: float
    pip_value_per_lot: float
    fx_rate: float
    raw_lots: float
    lots: float
    units: float
    max_loss: float
    actual_risk_pct: float
    target_pips: float | None
    rr: float | None
    potential_profit: float | None
    notional: float | None
    leverage: float | None
    violations: list[Violation]
    notes: list[str]


class DailyStatusOut(BaseModel):
    today: date
    currency: str
    starting_balance: float
    current_equity: float
    start_of_day_equity: float
    daily_loss_limit: float
    realized_pnl_today: float
    loss_used: float
    loss_used_ratio: float
    remaining_loss_budget: float
    open_risk: float
    open_trades: int
    trades_today: int
    max_trades_per_day: int
    checklist_complete: bool
    checklist_required: bool
    state: Literal["ok", "warning", "locked", "breached"]
    can_trade: bool
    reasons: list[Violation]


# --------------------------------------------------------------------------- trades


class TradeIn(BaseModel):
    status: Literal["open", "closed"] = "open"
    entry_time: datetime
    exit_time: datetime | None = None
    symbol: Annotated[str, StringConstraints(min_length=1, max_length=20, to_upper=True, strip_whitespace=True)]
    direction: Literal["long", "short"]
    entry_price: float = Field(gt=0)
    stop_loss: float = Field(gt=0)
    take_profit: float | None = Field(None, gt=0)
    exit_price: float | None = Field(None, gt=0)
    position_size: float | None = Field(None, gt=0)
    risk_amount: float | None = Field(None, ge=0)
    fees: float = 0.0
    pnl: float | None = None
    strategy: Annotated[str, StringConstraints(max_length=60, strip_whitespace=True)] = ""
    timeframe: Annotated[str, StringConstraints(max_length=10, strip_whitespace=True)] = ""
    session: Annotated[str, StringConstraints(max_length=40, strip_whitespace=True)] = ""
    confluences: list[Annotated[str, StringConstraints(max_length=80)]] = Field(default_factory=list, max_length=50)
    emotion: Annotated[str, StringConstraints(max_length=20, strip_whitespace=True)] = ""
    rule_compliant: bool | None = None
    tags: list[Annotated[str, StringConstraints(max_length=40, strip_whitespace=True)]] = Field(
        default_factory=list, max_length=30
    )
    notes: LongText = ""

    @model_validator(mode="after")
    def _geometry(self) -> TradeIn:
        long = self.direction == "long"
        if long and self.stop_loss >= self.entry_price:
            raise ValueError("For a long trade the stop loss must be below the entry price")
        if not long and self.stop_loss <= self.entry_price:
            raise ValueError("For a short trade the stop loss must be above the entry price")
        if self.take_profit is not None:
            if long and self.take_profit <= self.entry_price:
                raise ValueError("For a long trade the take profit must be above the entry price")
            if not long and self.take_profit >= self.entry_price:
                raise ValueError("For a short trade the take profit must be below the entry price")

        if self.status == "open":
            # An open position has no exit yet; ignore any stale exit fields.
            self.exit_price = None
            self.exit_time = None
            self.pnl = None
        else:
            if self.exit_price is None:
                raise ValueError("A closed trade needs an exit price")
            if self.exit_time is not None and _exit_before_entry(self.exit_time, self.entry_time):
                raise ValueError("Exit time cannot be before entry time")

        self.confluences = _dedupe(self.confluences)
        self.tags = _dedupe(self.tags)
        return self


class ScreenshotOut(ORMModel):
    id: int
    kind: Literal["file", "url"]
    src: str
    caption: str
    created_at: datetime | None = None


class ScreenshotUrlIn(BaseModel):
    url: Annotated[str, StringConstraints(max_length=1000, strip_whitespace=True)]
    caption: ShortText = ""

    @field_validator("url")
    @classmethod
    def _http_only(cls, value: str) -> str:
        if not value.lower().startswith(("http://", "https://")):
            raise ValueError("Screenshot URL must start with http:// or https://")
        return value


class TradeOut(ORMModel):
    id: int
    status: str
    entry_time: datetime
    exit_time: datetime | None
    symbol: str
    direction: str
    entry_price: float
    stop_loss: float
    take_profit: float | None
    exit_price: float | None
    position_size: float | None
    risk_amount: float | None
    fees: float
    pnl: float | None
    pnl_manual: bool
    planned_rr: float | None
    executed_rr: float | None
    r_multiple: float | None
    outcome: str | None
    strategy: str
    timeframe: str
    session: str
    confluences: list[str]
    emotion: str
    rule_compliant: bool | None
    violations: list[Violation]
    tags: list[str]
    notes: str
    screenshots: list[ScreenshotOut]
    created_at: datetime | None = None
    updated_at: datetime | None = None


class TradeSaveOut(BaseModel):
    trade: TradeOut
    warnings: list[Violation]


class TradeListOut(BaseModel):
    items: list[TradeOut]
    total: int


class TradeCheckOut(BaseModel):
    blocked: bool
    violations: list[Violation]
    planned_rr: float | None
    risk_amount: float | None
    risk_pct: float | None
    session: str


def _exit_before_entry(exit_time: datetime, entry_time: datetime) -> bool:
    """True when ``exit_time`` precedes ``entry_time`` (mixed naive/aware tolerated)."""
    if (exit_time.tzinfo is None) != (entry_time.tzinfo is None):
        exit_time, entry_time = exit_time.replace(tzinfo=None), entry_time.replace(tzinfo=None)
    return exit_time < entry_time


def _dedupe(values: list[str]) -> list[str]:
    seen: dict[str, None] = {}
    for value in values:
        value = value.strip()
        if value:
            seen.setdefault(value, None)
    return list(seen)
