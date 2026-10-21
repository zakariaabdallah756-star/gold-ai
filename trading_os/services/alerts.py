"""Strategy alert engine: scan market data for user-configured strategy setups.

A rule (``AlertRule``) watches symbols on one timeframe. On each scan the
engine loads the latest CLOSED candles (``services.market_data``), evaluates
the rule's conditions (``services.detectors``) on every candle that closed
since the previous scan (``AlertRule.scan_state``; at most ``MAX_CATCH_UP``)
and, when enough of them line up in one direction and the Strategy Co-Pilot
accepts the setup, records a ``StrategyAlert`` with entry, stop, targets and
size and sends a push notification (``services.notify``).

Alerts are informational only: the engine never creates, logs or executes a
trade. The guardrail pre-check runs ``services.trades.evaluate`` on a
hypothetical entry purely to tell the trader what would block it.

``register_jobs(scheduler, config)`` registers the background job
``alert-scanner``; it scans only while ``AccountSettings.scanner_enabled`` is
on. ``scan_now(scheduler)`` runs the same job on demand, whatever the switch.
"""

from __future__ import annotations

import logging
import math
import threading
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from ..constants import DEFAULT_TP_SPLITS
from ..models import AccountSettings, AlertRule, ChecklistDay, Market, Strategy, StrategyAlert, TradingSession
from ..schemas import TargetIn, TradeIn
from . import detectors, market_data, notify, trades
from .account import current_equity, get_settings, local_now
from .copilot import TradeContext, strategy_violations
from .guardrails import Violation
from .sessions import classify_session
from .sizing import SizingError, blended_rr, calculate_position_size

log = logging.getLogger("trading_os.alerts")

JOB_NAME = "alert-scanner"
STOP_METHODS = ("swing", "atr", "signal")
DIRECTION_FILTERS = ("both", "long", "short")
ALERT_STATUSES = ("new", "seen", "dismissed", "taken")
MIN_INTERVAL, MAX_INTERVAL = 30, 3600
SWING_WINDOW = 20  # bars searched for the protective swing
SWING_STRENGTH = 2
ATR_PERIOD = 14
MAX_ERROR = 300
MAX_CATCH_UP = 5  # newly closed candles evaluated per symbol and scan (missed by scan drift or long intervals)
NO_EXECUTION = "Nothing was executed - the decision is yours."
FX_CODES = frozenset("USD EUR GBP JPY AUD NZD CAD CHF SEK NOK DKK PLN CZK HUF TRY ZAR MXN SGD HKD CNH CNY ILS".split())

_manual = threading.local()  # set while scan_now runs the job in the caller's thread


def clamp_interval(seconds: int | float | None) -> int:
    return int(max(MIN_INTERVAL, min(MAX_INTERVAL, seconds or 60)))


# --------------------------------------------------------------------------- price helpers


def pip_size_for(market: Market | None, symbol: str, price: float) -> float:
    """The market's pip size, or a sensible guess when the symbol is not configured."""
    if market is not None and market.pip_size > 0:
        return market.pip_size
    if len(symbol) == 6 and symbol[:3] in FX_CODES and symbol[3:] in FX_CODES:  # a currency pair
        return 0.01 if symbol.endswith("JPY") else 0.0001
    return 10.0 ** (math.floor(math.log10(price)) - 4) if price > 0 else 0.01


def price_decimals(pip: float) -> int:
    return max(0, round(-math.log10(pip)) + 1) if pip > 0 else 2


def signal_text(symbol: str, direction: str, entry: float, stop: float, targets: list[float], decimals: int) -> str:
    """A plain signal message the Signals page parses back to the same trade plan."""
    lines = [f"{symbol} {'BUY' if direction == 'long' else 'SELL'} @ {entry:.{decimals}f}", f"SL {stop:.{decimals}f}"]
    lines += [f"TP{index} {price:.{decimals}f}" for index, price in enumerate(targets, start=1)]
    return "\n".join(lines)


# --------------------------------------------------------------------------- evaluation


@dataclass
class ConditionCheck:
    type: str
    label: str  # condition type label, e.g. "Liquidity sweep"
    confluence: str  # confluence name this condition counts as
    met: bool
    direction: str | None
    explanation: str
    stop_hint: float | None = None
    details: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "type": self.type,
            "label": self.label,
            "confluence": self.confluence,
            "met": self.met,
            "direction": self.direction,
            "explanation": self.explanation,
            "stop_hint": self.stop_hint,
            "details": {k: (round(v, 6) if isinstance(v, float) else v) for k, v in self.details.items()},
        }


@dataclass
class Levels:
    entry: float
    stop_loss: float
    take_profits: list[float]
    close_pcts: list[float]
    targets_rr: list[float]
    planned_rr: float | None
    stop_note: str
    decimals: int


@dataclass
class Evaluation:
    """Diagnostics for one rule on one symbol; ``found`` when it is an alertable setup."""

    symbol: str
    timeframe: str
    signal_time: datetime | None = None  # open time of the signal candle
    candle_time: datetime | None = None  # close time of the signal candle
    late: bool = False  # a candle caught up on after a newer one had closed
    close: float | None = None
    conditions: list[ConditionCheck] = field(default_factory=list)
    direction: str | None = None
    levels: Levels | None = None
    session: str = ""
    strategy: Strategy | None = None
    copilot: list[Violation] | None = None  # None = the rule has no strategy
    lots: float | None = None
    risk_amount: float | None = None
    risk_pct: float | None = None
    sizing_note: str = ""
    precheck: list[dict] | None = None
    reason: str = ""  # why this is not a setup ('' when found)

    @property
    def found(self) -> bool:
        return not self.reason and self.levels is not None

    @property
    def confluences(self) -> list[str]:
        return list(dict.fromkeys(c.confluence for c in self.conditions if c.met))

    @property
    def in_test(self) -> bool:
        return self.strategy is not None and self.strategy.status == "incubator"

    def to_dict(self) -> dict:
        levels = self.levels
        return {
            "symbol": self.symbol,
            "timeframe": self.timeframe,
            "signal_time": self.signal_time,
            "candle_time": self.candle_time,
            "close": self.close,
            "conditions": [c.to_dict() for c in self.conditions],
            "direction": self.direction,
            "found": self.found,
            "reason": self.reason,
            "confluences": self.confluences,
            "session": self.session,
            "strategy": self.strategy.name if self.strategy else "",
            "in_test": self.in_test,
            "levels": (
                None
                if levels is None
                else {
                    "entry": levels.entry,
                    "stop_loss": levels.stop_loss,
                    "take_profits": levels.take_profits,
                    "close_pcts": levels.close_pcts,
                    "targets_rr": levels.targets_rr,
                    "planned_rr": levels.planned_rr,
                    "stop_note": levels.stop_note,
                }
            ),
            "copilot": None if self.copilot is None else [v.to_dict() for v in self.copilot],
            "copilot_passes": None if self.copilot is None else not _copilot_blockers(self.copilot),
            "lots": self.lots,
            "risk_amount": self.risk_amount,
            "risk_pct": self.risk_pct,
            "sizing_note": self.sizing_note,
            "precheck": self.precheck,
        }


def _copilot_blockers(violations: list[Violation]) -> list[Violation]:
    """Co-Pilot findings that stop an alert: everything except the incubator notice."""
    return [v for v in violations if v.code != "STRATEGY_INCUBATOR"]


def _check_conditions(rule: AlertRule, bars: list, context: dict) -> list[ConditionCheck]:
    checks = []
    for condition in rule.conditions or []:
        kind = condition.get("type", "")
        spec = detectors.CONDITION_TYPES.get(kind)
        if spec is None:
            checks.append(ConditionCheck(kind, kind, kind, False, None, f"Unknown condition type '{kind}'."))
            continue
        try:
            result = detectors.run(kind, bars, condition.get("params") or {}, context)
        except ValueError as exc:  # parameters saved before a bound was tightened
            checks.append(ConditionCheck(kind, spec.label, spec.label, False, None, f"{exc} - edit the rule."))
            continue
        checks.append(
            ConditionCheck(
                kind,
                spec.label,
                (condition.get("confluence") or "").strip() or spec.label,
                result.met,
                result.direction,
                result.label,
                result.stop_hint,
                result.details,
            )
        )
    return checks


def _protective_swing(bars: list, direction: str, entry: float) -> tuple[float | None, str]:
    window = bars[-SWING_WINDOW:]
    highs, lows = detectors.swings(window, SWING_STRENGTH)
    if direction == "long":
        below = [price for _, price in lows if price < entry]
        if below:
            return below[-1], "swing low"
        extreme = min(bar.low for bar in window)
        return (extreme, f"{len(window)}-candle low") if extreme < entry else (None, "")
    above = [price for _, price in highs if price > entry]
    if above:
        return above[-1], "swing high"
    extreme = max(bar.high for bar in window)
    return (extreme, f"{len(window)}-candle high") if extreme > entry else (None, "")


def compute_levels(
    settings: AccountSettings,
    rule: AlertRule,
    strategy: Strategy | None,
    direction: str,
    bars: list,
    checks: list[ConditionCheck],
    pip: float,
) -> tuple[Levels | None, str]:
    """Entry (last close), stop by the rule's method plus buffer, and targets in R; or ``(None, reason)``.

    Prices are rounded to the market's precision; targets round away from the entry, so the plan's R:R is
    never a hair below the multiple asked for (which would fail a minimum R:R check).
    """
    decimals = price_decimals(pip)
    entry = round(bars[-1].close, decimals)
    sign = 1 if direction == "long" else -1
    base, note = None, ""
    method = rule.stop_method
    if method == "signal":
        hints = [c.stop_hint for c in checks if c.met and c.stop_hint is not None and c.direction in (direction, None)]
        hints = [hint for hint in hints if (entry - hint) * sign > 0]
        if hints:
            base = min(hints) if direction == "long" else max(hints)
            note = "beyond the signal structure"
    if method == "atr":
        value = detectors.atr(bars, ATR_PERIOD)[-1]
        if value is None or value <= 0:
            return None, f"ATR stop needs at least {ATR_PERIOD} candles of range."
        base = entry - sign * rule.atr_multiple * value
        note = f"{rule.atr_multiple:g} x ATR {ATR_PERIOD}"
    if base is None:  # swing, or a signal stop without a usable structure level
        base, where = _protective_swing(bars, direction, entry)
        if base is None:
            return (
                None,
                f"No swing {'low below' if sign > 0 else 'high above'} the entry within {SWING_WINDOW} candles.",
            )
        note = f"beyond the {where}" + (" (no signal level)" if method == "signal" else "")
    if rule.stop_buffer_pips:
        note += f" + {rule.stop_buffer_pips:g} pips buffer"
    stop = round(base - sign * rule.stop_buffer_pips * pip, decimals)
    risk = (entry - stop) * sign
    if risk <= 0:
        return None, "The stop would be at or beyond the entry - no valid setup."

    multiples = list(rule.targets_rr or []) or [
        max(settings.min_rr or 0, (strategy.min_rr if strategy else 0) or 0) or 1.0
    ]
    targets = []
    for multiple in multiples:
        exact = entry + sign * multiple * risk
        price = round(exact, decimals)
        if (price - exact) * sign < -1e-9 * max(1.0, abs(exact)):  # rounded towards the entry: one tick out
            price = round(price + sign * 10.0**-decimals, decimals)
        targets.append(price)
    if stop <= 0 or any(price <= 0 for price in targets):
        return None, "The stop or a target would be at or below zero - no valid setup at this price."
    if any((b - a) * sign <= 0 for a, b in zip([entry, *targets[:-1]], targets, strict=True)):
        return None, "The stop is too tight to place distinct targets at this price precision."
    close_pcts = [float(p) for p in DEFAULT_TP_SPLITS[len(targets)]]
    rr = blended_rr(entry, stop, list(zip(targets, close_pcts, strict=True)))
    rr = round(rr, 4) if rr is not None else None
    return (
        Levels(entry, stop, targets, close_pcts, [float(r) for r in multiples], rr, note, decimals),
        "",
    )


def copilot_violations(
    settings: AccountSettings,
    strategy: Strategy,
    direction: str,
    session: str,
    timeframe: str,
    confluences: list[str],
    planned_rr: float | None,
    bias: str,
) -> list[Violation]:
    """Strategy Co-Pilot findings for a setup, including the strategy's minimum number of confluences."""
    ctx = TradeContext(direction, session, timeframe, list(confluences), planned_rr, bias)
    violations = strategy_violations(settings, strategy, ctx)
    if strategy.min_confluences and len(confluences) < strategy.min_confluences:
        severity = "block" if settings.trade_rule_mode == "block" else "warn"
        violations.append(
            Violation(
                "CONFLUENCES_BELOW_MIN",
                f"Co-Pilot: {strategy.name} needs at least {strategy.min_confluences} confluences; "
                f"this setup has {len(confluences)}.",
                severity,
            )
        )
    return violations


def _size(db: Session, settings: AccountSettings, rule: AlertRule, ev: Evaluation, market: Market | None) -> None:
    levels = ev.levels
    risk_pct = rule.risk_pct or settings.default_risk_pct
    equity = current_equity(db, settings)
    ev.risk_pct = risk_pct
    ev.risk_amount = round(equity * risk_pct / 100, 2) if equity > 0 else None
    if market is None:
        ev.sizing_note = (
            f"{ev.symbol} is not one of your markets - add it under Trading Plan → Markets to size the position."
        )
        return
    try:
        result = calculate_position_size(
            market, settings.currency, equity, risk_pct, entry=levels.entry, stop=levels.stop_loss
        )
    except SizingError as exc:
        ev.sizing_note = str(exc)
        return
    if result.lots > 0:
        ev.lots = result.lots
        ev.risk_amount = round(result.max_loss, 2)
    ev.sizing_note = " ".join(result.notes)


def guardrail_precheck(db: Session, settings: AccountSettings, ev: Evaluation, now: datetime) -> list[dict]:
    """Every guardrail finding for a hypothetical entry now (informational - nothing is saved)."""
    levels = ev.levels
    data = TradeIn(
        status="open",
        entry_time=now,
        symbol=ev.symbol,
        direction=ev.direction,
        entry_price=levels.entry,
        stop_loss=levels.stop_loss,
        take_profit=levels.take_profits[-1],
        position_size=ev.lots,
        risk_amount=None if ev.lots else ev.risk_amount,
        strategy=ev.strategy.name if ev.strategy else "",
        timeframe=ev.timeframe,
        confluences=ev.confluences,
        source="signal",
        targets=[TargetIn(price=p, close_pct=c) for p, c in zip(levels.take_profits, levels.close_pcts, strict=True)],
    )
    return [v.to_dict() for v in trades.evaluate(db, settings, data).violations]


def evaluate(
    db: Session,
    settings: AccountSettings,
    rule: AlertRule,
    symbol: str,
    bars: list,
    now: datetime,
    *,
    diagnostics: bool = False,
) -> Evaluation:
    """Evaluate ``rule`` on the closed ``bars`` of ``symbol`` (signal candle last).

    The result always carries the per-condition diagnostics; ``found`` is True
    when the setup is alertable. With ``diagnostics`` the guardrail pre-check
    also runs for setups the Co-Pilot rejected (for the rule's Test view).
    """
    ev = Evaluation(symbol=symbol, timeframe=rule.timeframe)
    if not bars:
        ev.reason = "No closed candles yet."
        return ev
    last = bars[-1]
    ev.signal_time = last.time
    ev.candle_time = last.time + market_data.timeframe_delta(rule.timeframe)
    ev.close = last.close

    if rule.strategy_id is not None:
        ev.strategy = db.get(Strategy, rule.strategy_id)
        if ev.strategy is None:
            ev.reason = "The rule's strategy no longer exists - edit the rule."
            return ev
        if ev.strategy.status == "retired":
            ev.reason = f"{ev.strategy.name} is retired - retired strategies never alert."
            return ev

    today = db.get(ChecklistDay, now.date())
    context = {"key_levels": detectors.parse_levels(today.key_levels) if today else []}
    ev.conditions = _check_conditions(rule, bars, context)

    met = [c for c in ev.conditions if c.met]
    total = len(ev.conditions)
    required = total if not rule.min_conditions else min(rule.min_conditions, total)
    if not total:
        ev.reason = "The rule has no conditions."
        return ev
    if len(met) < required:
        ev.reason = f"{len(met)} of {total} conditions met (needs {required})."
        return ev

    sides = {c.direction for c in met if c.direction}
    if len(sides) > 1:
        longs = ", ".join(c.label for c in met if c.direction == "long")
        shorts = ", ".join(c.label for c in met if c.direction == "short")
        ev.reason = f"Conditions disagree (long: {longs}; short: {shorts}) - no alert."
        return ev
    if not sides:
        ev.reason = "Only neutral filters are met - a directional condition must point long or short."
        return ev
    ev.direction = sides.pop()
    if rule.direction != "both" and ev.direction != rule.direction:
        ev.reason = f"{ev.direction.capitalize()} setup skipped - this rule only alerts {rule.direction} setups."
        return ev

    market = db.scalar(select(Market).where(Market.symbol == symbol))
    pip = pip_size_for(market, symbol, last.close)
    ev.levels, reason = compute_levels(settings, rule, ev.strategy, ev.direction, bars, ev.conditions, pip)
    if ev.levels is None:
        ev.reason = reason
        return ev

    windows = list(db.scalars(select(TradingSession)))
    ev.session = classify_session(ev.candle_time, settings.timezone, windows)
    if ev.strategy is not None:
        ev.copilot = copilot_violations(
            settings,
            ev.strategy,
            ev.direction,
            ev.session,
            rule.timeframe,
            ev.confluences,
            ev.levels.planned_rr,
            today.bias if today else "",
        )
        if _copilot_blockers(ev.copilot):
            ev.reason = f"Co-Pilot: the setup does not meet every {ev.strategy.name} requirement yet."

    if ev.found or diagnostics:
        _size(db, settings, rule, ev, market)
        ev.precheck = guardrail_precheck(db, settings, ev, now)
    return ev


# --------------------------------------------------------------------------- alerts & notifications


def precheck_line(precheck: list[dict]) -> str:
    """One line for the notification: all guardrails pass, or the blocking reasons."""
    blocking = [v["message"] for v in precheck if v.get("severity") == "block"]
    if not blocking:
        warnings = sum(1 for v in precheck if v.get("severity") == "warn")
        extra = f" ({warnings} warning{'s' if warnings != 1 else ''})" if warnings else ""
        return f"Pre-check: all guardrails pass{extra}."
    return "Pre-check: blocked - " + " · ".join(blocking)


def _fmt(value: float, decimals: int) -> str:
    return f"{value:.{decimals}f}"


def build_note(settings: AccountSettings, rule: AlertRule, alert: StrategyAlert, ev: Evaluation) -> notify.Note:
    levels = ev.levels
    d = levels.decimals
    head = ev.strategy.name if ev.strategy else rule.name
    targets = " · ".join(f"TP{i} {_fmt(tp, d)}" for i, tp in enumerate(levels.take_profits, start=1))
    size = f"{ev.lots:g} lots" if ev.lots else "size: see Signals"
    risk = f"risk {ev.risk_amount:,.2f} {settings.currency} ({ev.risk_pct:g}%)" if ev.risk_amount else ""
    lines = [
        f"Confluences: {', '.join(ev.confluences)}",
        f"Entry {_fmt(levels.entry, d)} · SL {_fmt(levels.stop_loss, d)} · {targets}",
        " · ".join(part for part in (f"R:R 1:{levels.planned_rr:.2f}", size, risk) if part),
        precheck_line(ev.precheck or []),
    ]
    if ev.late:
        lines.insert(0, f"Signal candle closed at {ev.candle_time:%H:%M} - price may have moved since.")
    if ev.in_test:
        lines.insert(0, f"In test: {ev.strategy.name} is still in the incubator - consider reduced size.")
    lines.append(NO_EXECUTION)
    return notify.Note(
        event="strategy_alert",
        title=f"{head} · {ev.symbol} {ev.direction.upper()} · {ev.timeframe}",
        body="\n".join(lines),
        url=f"/#/alerts?id={alert.id}",
        priority="high",
        data={
            "alert_id": alert.id,
            "symbol": ev.symbol,
            "direction": ev.direction,
            "entry": levels.entry,
            "stop_loss": levels.stop_loss,
            "take_profits": list(levels.take_profits),
        },
        tags=["In test"] if ev.in_test else [],
    )


def record_alert(
    db: Session, settings: AccountSettings, rule: AlertRule, ev: Evaluation, now: datetime
) -> StrategyAlert:
    """Persist a found setup as a new alert and notify (when the rule asks for it). Never creates a trade."""
    levels = ev.levels
    alert = StrategyAlert(
        rule_id=rule.id,
        created_at=now,
        candle_time=ev.candle_time,
        strategy=ev.strategy.name if ev.strategy else "",
        symbol=ev.symbol,
        timeframe=ev.timeframe,
        direction=ev.direction,
        entry_price=levels.entry,
        stop_loss=levels.stop_loss,
        take_profits=list(levels.take_profits),
        planned_rr=levels.planned_rr,
        lots=ev.lots,
        risk_amount=ev.risk_amount,
        confluences=ev.confluences,
        details={
            "rule_name": rule.name,
            "provider": rule.provider,
            "provider_label": market_data.PROVIDERS[rule.provider].label,
            "signal_time": ev.signal_time.isoformat(),
            "session": ev.session,
            "in_test": ev.in_test,
            "conditions": [c.to_dict() for c in ev.conditions],
            "copilot": [v.to_dict() for v in ev.copilot or []],
            "precheck": ev.precheck or [],
            "close_pcts": levels.close_pcts,
            "targets_rr": levels.targets_rr,
            "stop_note": levels.stop_note,
            "risk_pct": ev.risk_pct,
            "sizing_note": ev.sizing_note,
        },
        signal_text=signal_text(
            ev.symbol, ev.direction, levels.entry, levels.stop_loss, levels.take_profits, levels.decimals
        ),
        status="new",
    )
    db.add(alert)
    db.commit()  # never hold the database write lock while notifications are on the network
    if rule.notify:
        entry = notify.send(db, settings, build_note(settings, rule, alert, ev))
        if entry is not None:
            alert.notification_id = entry.id
            db.commit()
    return alert


# --------------------------------------------------------------------------- scanning


def stale_after(timeframe: str) -> timedelta:
    """How old the newest closed candle may be before the data counts as stale (market closed / delayed)."""
    return max(3 * market_data.timeframe_delta(timeframe), timedelta(minutes=30))


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _from_iso(value) -> datetime | None:
    try:
        return datetime.fromisoformat(value) if value else None
    except (TypeError, ValueError):
        return None


def _scan_symbol(
    db: Session, settings: AccountSettings, rule: AlertRule, symbol: str, now: datetime
) -> tuple[dict, datetime | None]:
    """Evaluate the newest closed candle of ``symbol`` and any earlier ones that closed since the last scan.

    Each candle alerts at most once, even when its alert was deleted: ``rule.scan_state`` remembers per
    symbol the newest evaluated candle (``through``), the last alerted candle (``candle``) and when that
    alert was sent (``alerted``, for the cool-down).
    """
    row = {
        "rule_id": rule.id,
        "rule_name": rule.name,
        "symbol": symbol,
        "status": "no_setup",
        "message": "",
        "alert_id": None,
    }
    bars = market_data.fetch_candles(
        db, settings, rule.provider, symbol, rule.timeframe, provider_symbols=rule.provider_symbols, now=now
    )
    delta = market_data.timeframe_delta(rule.timeframe)
    state = dict(rule.scan_state or {})
    memory = dict(state.get(symbol) or {})
    through = _from_iso(memory.get("through"))
    alerted = _from_iso(memory.get("alerted"))
    alerted_candle = _from_iso(memory.get("candle"))
    candle_close = bars[-1].time + delta
    memory["through"] = _iso(max(filter(None, (through, candle_close))))
    rule.scan_state = {**state, symbol: memory}
    if now - candle_close > stale_after(rule.timeframe):
        row["message"] = (
            f"Latest closed candle ended {candle_close:%Y-%m-%d %H:%M} - market closed or data delayed, nothing to alert."
        )
        return row, candle_close

    last = len(bars) - 1
    fresh = [
        index
        for index in range(max(0, last - MAX_CATCH_UP + 1), last + 1)
        if through is not None
        and bars[index].time + delta > through
        and now - (bars[index].time + delta) <= stale_after(rule.timeframe)
    ]
    alert_row = None
    for index in sorted(set(fresh) | {last}):
        ev = evaluate(db, settings, rule, symbol, bars[: index + 1], now)
        ev.late = index != last
        if not ev.found:
            outcome = {"status": "no_setup", "message": ev.reason, "alert_id": None}
        else:
            duplicate = db.scalar(
                select(StrategyAlert.id).where(
                    StrategyAlert.rule_id == rule.id,
                    StrategyAlert.symbol == symbol,
                    StrategyAlert.candle_time == ev.candle_time,
                )
            )
            previous = db.scalar(
                select(func.max(StrategyAlert.created_at)).where(
                    StrategyAlert.rule_id == rule.id, StrategyAlert.symbol == symbol
                )
            )
            previous = max(filter(None, (previous, alerted)), default=None)
            if duplicate is not None or (alerted_candle is not None and ev.candle_time <= alerted_candle):
                outcome = {"status": "duplicate", "message": "Already alerted for this candle.", "alert_id": duplicate}
            elif (
                rule.cooldown_minutes
                and previous is not None
                and now - previous < timedelta(minutes=rule.cooldown_minutes)
            ):
                until = previous + timedelta(minutes=rule.cooldown_minutes)
                outcome = {
                    "status": "cooldown",
                    "message": f"Setup found, but the rule is cooling down until {until:%H:%M}.",
                    "alert_id": None,
                }
            else:
                alerted, alerted_candle = now, ev.candle_time
                memory.update(alerted=_iso(now), candle=_iso(ev.candle_time))
                rule.scan_state = {**state, symbol: dict(memory)}
                rule.last_alert_at = now
                alert = record_alert(db, settings, rule, ev, now)
                outcome = {
                    "status": "alert",
                    "message": f"{ev.direction.upper()} setup at {ev.levels.entry:g}"
                    + (f" (candle closed {ev.candle_time:%H:%M})" if ev.late else ""),
                    "alert_id": alert.id,
                }
                alert_row = outcome
        if index == last:
            row.update(outcome)
    if alert_row is not None:
        row.update(alert_row)
    return row, candle_close


def _rule_exists(db: Session, rule_id: int) -> bool:
    return db.scalar(select(AlertRule.id).where(AlertRule.id == rule_id)) is not None


def scan(db: Session, settings: AccountSettings, now: datetime, rule_ids: list[int] | None = None) -> list[dict]:
    """Scan every enabled rule (or the enabled rules in ``rule_ids``) on each of its symbols.

    One failing symbol or provider never stops the others: its row gets status
    ``error`` and the rule keeps the message in ``last_error``. A rule deleted
    while it is being scanned is skipped.
    """
    query = select(AlertRule).where(AlertRule.enabled.is_(True)).order_by(AlertRule.id)
    if rule_ids is not None:
        query = query.where(AlertRule.id.in_(rule_ids))
    results: list[dict] = []
    for rule in db.scalars(query).all():
        rule_id, rule_name, symbols = rule.id, rule.name, list(rule.symbols or [])
        errors: list[str] = []
        latest: datetime | None = None
        gone = False
        for symbol in symbols:
            row = {"rule_id": rule_id, "rule_name": rule_name, "symbol": symbol, "status": "error", "alert_id": None}
            try:
                row, candle_close = _scan_symbol(db, settings, rule, symbol, now)
                db.commit()  # keep each alert even if a later symbol fails
                latest = max(filter(None, (latest, candle_close)), default=None)
            except market_data.MarketDataError as exc:
                db.rollback()
                row["message"] = str(exc)
                errors.append(f"{symbol}: {exc}")
            except Exception as exc:  # a broken symbol must not stop the scan
                db.rollback()
                if not _rule_exists(db, rule_id):
                    gone = True
                    break
                log.exception("Alert scan failed for rule %s on %s", rule_id, symbol)
                row["message"] = f"Unexpected error: {type(exc).__name__}"
                errors.append(f"{symbol}: unexpected error ({type(exc).__name__})")
            results.append(row)
        if gone or not _rule_exists(db, rule_id):
            continue
        try:
            rule.last_checked_at = now
            if latest is not None:
                rule.last_candle_at = latest
            rule.last_error = "; ".join(errors)[:MAX_ERROR]
            db.commit()
        except SQLAlchemyError:  # deleted between the check and the commit
            db.rollback()
    return results


def scan_summary(db: Session, settings: AccountSettings, now: datetime, *, manual: bool) -> dict:
    results = scan(db, settings, now)
    return {
        "ran_at": now,
        "manual": manual,
        "alerts": sum(1 for row in results if row["status"] == "alert"),
        "errors": sum(1 for row in results if row["status"] == "error"),
        "results": results,
    }


def dry_run(db: Session, settings: AccountSettings, rule: AlertRule, now: datetime) -> list[dict]:
    """Evaluate ``rule`` on the latest data of each symbol without saving or notifying."""
    out = []
    for symbol in rule.symbols or []:
        item = {
            "symbol": symbol,
            "provider_symbol": market_data.provider_symbol(rule.provider, symbol, rule.provider_symbols),
        }
        try:
            bars = market_data.fetch_candles(
                db, settings, rule.provider, symbol, rule.timeframe, provider_symbols=rule.provider_symbols, now=now
            )
            ev = evaluate(db, settings, rule, symbol, bars, now, diagnostics=True)
        except market_data.MarketDataError as exc:
            out.append({**item, "error": str(exc)})
            continue
        except Exception as exc:  # one broken symbol must not break the whole test
            log.exception("Alert rule test failed for rule %s on %s", rule.id, symbol)
            out.append({**item, "error": f"Unexpected error: {type(exc).__name__}"})
            continue
        stale = ev.candle_time is not None and now - ev.candle_time > stale_after(rule.timeframe)
        out.append({**item, "error": "", "bars": len(bars), "stale": stale, **ev.to_dict()})
    return out


def register_jobs(scheduler, config) -> None:
    """Register the background scanner; it scans only while the scanner switch is on."""

    def interval() -> float:
        with scheduler.session_factory() as db:
            return float(clamp_interval(get_settings(db).scanner_interval_sec))

    def run(session_factory) -> dict | None:
        manual = getattr(_manual, "active", False)
        with session_factory() as db:
            settings = get_settings(db)
            if not (settings.scanner_enabled or manual):
                return job.last_result  # keep the last scan's results while the switch is off
            return scan_summary(db, settings, local_now(settings), manual=manual)

    job = scheduler.register(
        JOB_NAME, interval, run, description="Strategy alert scanner (closed candles only)", initial_delay=10.0
    )


def scan_now(scheduler):
    """Run the scanner job now in this thread, whether or not the background switch is on."""
    _manual.active = True
    try:
        return scheduler.run_now(JOB_NAME)
    finally:
        _manual.active = False
