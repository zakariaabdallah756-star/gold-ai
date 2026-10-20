"""Quick signal parser and risk-checked signal preview.

* ``POST /api/signals/parse``   free text -> parsed fields, the editable ticket draft and a full preview.
* ``POST /api/signals/preview`` edited ticket -> sizing, scale-out targets, guardrail check and the
  exact ``ticket`` body to ``POST /api/trades``.

Content problems (nothing recognised, missing stop, unknown symbol ...) are
reported in ``errors`` with HTTP 200; 422 only means a malformed request.
Execution is a normal ``POST /api/trades`` with the ticket, where the server
enforces every guardrail again (423 when blocked).

This module intentionally does not use ``from __future__ import annotations``
(FastAPI resolves the request/response models at import time).
"""

from typing import Annotated, Literal

from fastapi import APIRouter
from pydantic import BaseModel, Field, StringConstraints, ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..constants import MAX_TARGETS
from ..models import AccountSettings, Market
from ..schemas import MarketOut, TradeCheckOut, TradeIn
from ..services import account
from ..services import trades as trade_service
from ..services.signal_parser import ParsedSignal, parse_signal, resolve_close_pcts, split_lots
from ..services.sizing import SizingError, blended_rr, calculate_position_size, gross_pnl, risk_for_lots, round_lots
from .deps import DbDep, SettingsDep

router = APIRouter(prefix="/api", tags=["signals"])

SignalText = Annotated[str, StringConstraints(max_length=20_000)]
# Numbers are range-checked in ``build_preview`` so impossible values come back as content errors (HTTP 200).
Number = Annotated[float, Field(allow_inf_nan=False)]
StrategyName = Annotated[str, StringConstraints(max_length=60, strip_whitespace=True)]
Timeframe = Annotated[str, StringConstraints(max_length=10, strip_whitespace=True)]
Confluences = Annotated[list[Annotated[str, StringConstraints(max_length=80)]], Field(max_length=50)]
NOTHING_RECOGNISED = "No trade signal recognised - paste a message with a symbol, BUY/SELL, entry, SL and TP."


# --------------------------------------------------------------------------- request / response models


class ParseIn(BaseModel):
    text: SignalText = ""
    risk_pct: Number | None = None
    account_equity: Number | None = None
    # Trade context the trader already chose; carried into the draft so the Co-Pilot checks it.
    strategy: StrategyName = ""
    timeframe: Timeframe = ""
    confluences: Confluences = []


class TargetDraft(BaseModel):
    price: Number | None = None
    pips: Number | None = None  # relative target, used while ``price`` is empty
    close_pct: Number | None = None  # blank = default split / share of the rest


class TicketDraft(BaseModel):
    """The editable signal ticket (body of ``/signals/preview``)."""

    symbol: Annotated[str, StringConstraints(max_length=20, strip_whitespace=True, to_upper=True)] = ""
    direction: Literal["long", "short"] | None = None  # None = infer from entry vs stop
    entry: Number | None = None
    stop_loss: Number | None = None
    stop_pips: Number | None = None  # relative stop, used while ``stop_loss`` is empty
    targets: list[TargetDraft] = Field(default_factory=list, max_length=MAX_TARGETS)
    risk_pct: Number | None = None  # None = settings.default_risk_pct
    account_equity: Number | None = None  # None = current equity
    lots: Number | None = None  # manual override of the computed size
    fx_rate: Number | None = None  # 1 quote unit = fx_rate account units
    be_enabled: bool | None = None  # None = settings.default_auto_be
    be_offset_pips: Number = 0.0
    trail_enabled: bool | None = None  # None = on when settings.default_trail_pips > 0
    trail_distance_pips: Number | None = None  # None = settings.default_trail_pips
    trail_trigger: Literal["tp1", "immediate"] = "tp1"
    strategy: StrategyName = ""
    timeframe: Timeframe = ""
    confluences: Confluences = []
    signal_text: SignalText = ""


class ParsedOut(BaseModel):
    symbol: str | None
    symbol_raw: str | None
    direction: Literal["long", "short"] | None
    order_type: Literal["market", "limit", "stop"]
    entry: float | None
    entry_low: float | None
    entry_high: float | None
    stop_loss: float | None
    stop_pips: float | None
    take_profits: list[float]
    target_pips: list[float]
    warnings: list[str]


class SizingOut(BaseModel):
    lots: float  # size used by the ticket (manual override when given)
    auto_lots: float  # size from the risk rules
    manual: bool
    raw_lots: float
    risk_pct: float  # risk % the size was computed for
    account_equity: float
    risk_amount: float  # money the risk % allows
    max_loss: float  # money lost at the stop with ``lots``
    actual_risk_pct: float
    pip_value_per_lot: float
    stop_pips: float
    notes: list[str]


class TargetPlanOut(BaseModel):
    level: int
    price: float
    close_pct: float
    pips: float | None
    lots: float | None
    rr: float | None
    profit: float | None


class SignalPreviewOut(BaseModel):
    parsed: ParsedOut | None = None  # /signals/parse only
    draft: TicketDraft | None = None  # /signals/parse only: the ticket fields to edit
    market: MarketOut | None
    needs_fx: bool
    errors: list[str]
    warnings: list[str]
    sizing: SizingOut | None
    targets: list[TargetPlanOut]
    blended_rr: float | None
    potential_profit: float | None
    check: TradeCheckOut | None
    ticket: dict | None  # exact body for POST /api/trades; None until the ticket is complete


# --------------------------------------------------------------------------- endpoints


@router.post("/signals/parse", response_model=SignalPreviewOut)
def parse(payload: ParseIn, settings: SettingsDep, db: DbDep):
    """Parse a pasted signal and preview it with the account's risk rules."""
    parsed = parse_signal(payload.text, db.scalars(select(Market.symbol).order_by(Market.symbol)))
    draft = draft_from_parsed(parsed, payload, settings)
    if not (parsed.symbol or parsed.direction or parsed.stop_loss or parsed.stop_pips or parsed.take_profits):
        preview = _empty_preview([NOTHING_RECOGNISED if payload.text.strip() else "Paste a signal to start."])
    else:
        preview = build_preview(db, settings, draft)
    return {**preview, "parsed": parsed.to_dict(), "draft": draft}


@router.post("/signals/preview", response_model=SignalPreviewOut)
def preview(payload: TicketDraft, settings: SettingsDep, db: DbDep):
    """Size and guardrail-check an edited signal ticket."""
    return build_preview(db, settings, payload)


# --------------------------------------------------------------------------- preview


def draft_from_parsed(parsed: ParsedSignal, payload: ParseIn, settings: AccountSettings) -> TicketDraft:
    """Ticket fields from a parsed signal. Relative stops/targets stay relative (they follow the entry)."""
    targets = [TargetDraft(price=price) for price in parsed.take_profits]
    targets += [TargetDraft(pips=pips) for pips in parsed.target_pips]
    return TicketDraft(
        symbol=parsed.symbol or "",
        direction=parsed.direction,
        entry=parsed.entry,
        stop_loss=parsed.stop_loss,
        stop_pips=parsed.stop_pips if parsed.stop_loss is None else None,
        targets=targets[:MAX_TARGETS],
        risk_pct=payload.risk_pct,
        account_equity=payload.account_equity,
        be_enabled=settings.default_auto_be,
        trail_enabled=settings.default_trail_pips > 0,
        trail_distance_pips=settings.default_trail_pips or None,
        strategy=payload.strategy,
        timeframe=payload.timeframe,
        confluences=payload.confluences,
        signal_text=payload.text,
    )


def build_preview(db: Session, settings: AccountSettings, draft: TicketDraft) -> dict:
    """Sizing, scale-out plan, guardrail check and executable ticket for ``draft``."""
    out = _empty_preview([])
    errors, warnings = out["errors"], out["warnings"]
    currency = settings.currency
    draft = _checked_numbers(draft, errors)

    market = db.scalar(select(Market).where(Market.symbol == draft.symbol)) if draft.symbol else None
    if not draft.symbol:
        errors.append("No symbol - choose the instrument.")
    elif market is None:
        errors.append(f"{draft.symbol} is not one of your markets - add it in Trading Plan → Markets to trade it.")
    if market is not None:
        out["market"] = MarketOut.model_validate(market)
        out["needs_fx"] = market.quote_currency != currency and market.base_currency != currency
        if not market.active:
            errors.append(f"{market.symbol} is inactive - re-activate it in Trading Plan → Markets.")
            market = None
    if market is not None and out["needs_fx"] and not draft.fx_rate:
        errors.append(
            f"{market.symbol} is quoted in {market.quote_currency} - enter the "
            f"{market.quote_currency}→{currency} rate to size it."
        )
    pip = market.pip_size if market else None

    # ------------------------------------------------------------ geometry
    entry, stop, direction = draft.entry, draft.stop_loss, draft.direction
    stop_pips = draft.stop_pips if stop is None else None
    if direction is None and entry and stop and stop != entry:
        direction = "long" if stop < entry else "short"
    sign = _sign(direction)
    if stop is None and stop_pips and entry and sign and pip:
        stop = _price(entry - sign * stop_pips * pip)
    if entry is None:
        errors.append("No entry price - enter your fill price.")
    if stop is None and not stop_pips:
        errors.append("No stop loss found - cannot size the position.")
    elif entry is not None and stop == entry:
        errors.append("Stop loss equals the entry price.")
    if direction is None and not (stop_pips and entry is None):
        errors.append("Choose a direction (long or short).")

    # ------------------------------------------------------------ sizing
    sizing = None
    sizeable = market is not None and (draft.fx_rate or not out["needs_fx"])
    if sizeable and ((entry and stop and stop != entry) or (stop is None and stop_pips)):
        sizing = _sizing(db, settings, market, draft, entry, stop, stop_pips, errors)
        out["sizing"] = sizing
    lots = sizing["lots"] if sizing and sizing["lots"] > 0 else None

    # ------------------------------------------------------------ targets
    plan = _target_plan(draft, entry, stop, direction, pip, errors)
    if plan:
        risk = abs(entry - stop) if entry and stop and stop != entry else None
        lot_split = split_lots(lots, [pct for _, pct in plan], market.lot_step) if lots else [None] * len(plan)
        for level, ((price, pct), part) in enumerate(zip(plan, lot_split, strict=True), start=1):
            profit = None
            if part is not None and entry and direction:
                profit = gross_pnl(market, currency, direction, entry, price, part)
                if profit is None:  # quote currency needs the fx rate the sizing used
                    profit = sign * (price - entry) / pip * sizing["pip_value_per_lot"] * part
            out["targets"].append(
                {
                    "level": level,
                    "price": price,
                    "close_pct": pct,
                    "pips": abs(price - entry) / pip if entry and pip else None,
                    "lots": part,
                    "rr": abs(price - entry) / risk if risk else None,
                    "profit": profit,
                }
            )
            if part == 0:
                warnings.append(
                    f"At {lots:g} lots TP{level} closes nothing (its {pct:g}% is below the "
                    f"{market.lot_step:g} lot step)."
                )
        if risk:
            out["blended_rr"] = blended_rr(entry, stop, plan)
        profits = [t["profit"] for t in out["targets"]]
        if profits and all(p is not None for p in profits):
            out["potential_profit"] = sum(profits)

    # ------------------------------------------------------------ management
    be_enabled = settings.default_auto_be if draft.be_enabled is None else draft.be_enabled
    trail_enabled = settings.default_trail_pips > 0 if draft.trail_enabled is None else draft.trail_enabled
    trail_distance = draft.trail_distance_pips or settings.default_trail_pips or None
    if be_enabled and not plan:
        warnings.append("Break-even moves the stop when TP1 is hit - add a target or it never triggers.")
    if trail_enabled and draft.trail_trigger == "tp1" and not plan:
        warnings.append("The trailing stop starts at TP1 - add a target or start it immediately.")

    # ------------------------------------------------------------ ticket + guardrails
    if errors or lots is None or entry is None or stop is None:
        return out
    ticket = {
        "status": "open",
        "entry_time": account.local_now(settings).isoformat(),
        "symbol": market.symbol,
        "direction": direction,
        "entry_price": entry,
        "stop_loss": stop,
        "take_profit": plan[-1][0] if plan else None,
        "targets": [{"price": price, "close_pct": pct} for price, pct in plan],
        "position_size": lots,
        "be_enabled": be_enabled,
        "be_offset_pips": draft.be_offset_pips,
        "trail_enabled": trail_enabled,
        "trail_distance_pips": trail_distance if trail_enabled else None,
        "trail_trigger": draft.trail_trigger,
        "source": "signal",
        "signal_text": draft.signal_text,
        "strategy": draft.strategy,
        "timeframe": draft.timeframe,
        "confluences": list(draft.confluences),
        "emotion": "",
    }
    if out["needs_fx"]:  # the server cannot convert this quote currency on its own
        ticket["risk_amount"] = round(sizing["max_loss"], 2)
    try:
        trade_in = TradeIn.model_validate(ticket)
    except ValidationError as exc:
        errors.extend(_validation_messages(exc))
        return out
    ev = trade_service.evaluate(db, settings, trade_in)
    out["check"] = {
        "blocked": ev.blocked,
        "violations": [v.to_dict() for v in ev.violations],
        "planned_rr": ev.planned_rr,
        "risk_amount": ev.risk_amount,
        "risk_pct": ev.risk_pct,
        "session": ev.session,
    }
    out["ticket"] = ticket
    return out


def _sizing(
    db: Session,
    settings: AccountSettings,
    market: Market,
    draft: TicketDraft,
    entry: float | None,
    stop: float | None,
    stop_pips: float | None,
    errors: list[str],
) -> dict | None:
    equity = draft.account_equity or account.current_equity(db, settings)
    risk_pct = draft.risk_pct or settings.default_risk_pct
    try:
        result = calculate_position_size(
            market,
            settings.currency,
            equity,
            risk_pct,
            entry=entry,
            stop=stop,
            stop_pips=stop_pips,
            fx_rate=draft.fx_rate,
        )
    except SizingError as exc:
        errors.append(str(exc))
        return None

    lots, max_loss, notes = result.lots, result.max_loss, list(result.notes)
    if draft.lots is not None:
        lots = round_lots(draft.lots, market)
        if lots == 0:
            errors.append(f"{draft.lots:g} lots is below the minimum lot size of {market.min_lot:g}.")
        elif abs(lots - draft.lots) > 1e-9:
            notes.append(
                f"Manual size adjusted to {lots:g} lots (lot step {market.lot_step:g}, max {market.max_lot:g})."
            )
        manual_loss = risk_for_lots(market, settings.currency, entry, stop, lots) if entry and stop else None
        max_loss = manual_loss if manual_loss is not None else lots * result.stop_pips * result.pip_value_per_lot
    elif lots == 0:
        errors.append("Position size rounds to 0 lots at this risk - raise the risk % or enter the lots manually.")

    return {
        "lots": lots,
        "auto_lots": result.lots,
        "manual": draft.lots is not None,
        "raw_lots": result.raw_lots,
        "risk_pct": risk_pct,
        "account_equity": equity,
        "risk_amount": result.risk_amount,
        "max_loss": max_loss,
        "actual_risk_pct": max_loss / equity * 100.0,
        "pip_value_per_lot": result.pip_value_per_lot,
        "stop_pips": result.stop_pips,
        "notes": notes,
    }


def _target_plan(
    draft: TicketDraft,
    entry: float | None,
    stop: float | None,
    direction: str | None,
    pip: float | None,
    errors: list[str],
) -> list[tuple[float, float]]:
    """``[(price, close_pct), ...]`` with relative targets priced and blank close % filled in."""
    sign = _sign(direction)
    rows: list[tuple[float, float | None]] = []
    for target in draft.targets:
        price = target.price
        if price is None and target.pips:
            if not (entry and sign and pip):
                errors.append("Relative targets (pips) need the entry price, direction and a configured market.")
                return []
            price = _price(entry + sign * target.pips * pip)
        if price is not None:
            rows.append((price, target.close_pct))
    try:
        pcts = resolve_close_pcts([pct for _, pct in rows])
    except ValueError as exc:
        errors.append(str(exc))
        return []
    return [(price, pct) for (price, _), pct in zip(rows, pcts, strict=True)]


_POSITIVE_FIELDS = (
    ("entry", "Entry price"),
    ("stop_loss", "Stop loss"),
    ("stop_pips", "Stop distance"),
    ("risk_pct", "Risk %"),
    ("account_equity", "Account equity"),
    ("lots", "Lots"),
    ("fx_rate", "Conversion rate"),
    ("trail_distance_pips", "Trail distance"),
)
_TARGET_FIELDS = (("price", "price"), ("pips", "distance"), ("close_pct", "close %"))


def _checked_numbers(draft: TicketDraft, errors: list[str]) -> TicketDraft:
    """Report impossible numbers (0, negative, > 100 %) as content errors and leave them out of the preview."""
    update: dict = {}
    for name, label in _POSITIVE_FIELDS:
        value = getattr(draft, name)
        if value is not None and value <= 0:
            errors.append(f"{label} must be greater than 0.")
            update[name] = None
    if draft.risk_pct is not None and draft.risk_pct > 100:
        errors.append("Risk % cannot be more than 100.")
        update["risk_pct"] = None
    if draft.be_offset_pips < 0:
        errors.append("Break-even offset cannot be negative.")
        update["be_offset_pips"] = 0.0
    targets = []
    for level, target in enumerate(draft.targets, start=1):
        fixes = {}
        for name, label in _TARGET_FIELDS:
            value = getattr(target, name)
            if value is None:
                continue
            if name == "close_pct" and not 0 < value <= 100:
                errors.append(f"TP{level} {label} must be between 0 and 100.")
                fixes[name] = None
            elif value <= 0:
                errors.append(f"TP{level} {label} must be greater than 0.")
                fixes[name] = None
        targets.append(target.model_copy(update=fixes) if fixes else target)
    return draft.model_copy(update={**update, "targets": targets})


def _empty_preview(errors: list[str]) -> dict:
    return {
        "market": None,
        "needs_fx": False,
        "errors": errors,
        "warnings": [],
        "sizing": None,
        "targets": [],
        "blended_rr": None,
        "potential_profit": None,
        "check": None,
        "ticket": None,
    }


def _sign(direction: str | None) -> int:
    return {"long": 1, "short": -1}.get(direction or "", 0)


def _price(value: float) -> float:
    """Strip float noise from computed prices (2650 - 30 * 0.1)."""
    return round(value, 8)


def _validation_messages(exc: ValidationError) -> list[str]:
    messages = []
    for error in exc.errors():
        message = str(error.get("msg", "Invalid value")).removeprefix("Value error, ")
        loc = ".".join(str(part) for part in error.get("loc", ()))
        messages.append(f"{loc}: {message}" if loc and error.get("type") != "value_error" else message)
    return messages
