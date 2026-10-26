"""Automated execution of a bound strategy's alerts (v1.7).

A ``StrategyBinding`` links a strategy to a trading account. When the alert scanner records a setup for that
strategy (its conditions lined up and the Strategy Co-Pilot accepted it), and the binding is armed, this module
sizes the ticket for the binding's account and risk and sends it through ``services.execution.submit`` — the one
and only path that reaches a broker, so every rail there still applies:

* the account's own order sending must be switched on (which needs the typed account name for a real account);
* the full guardrail check runs first (checklist, daily loss, max trades, off-session, news, cool-off, prop
  rules, max risk) — a block sends nothing;
* the max-lots-per-order cap and idempotency hold.

On top of those, automation adds its own rails: the binding must be enabled and ``auto_execute`` armed (arming a
real account needs the typed account name too), a per-binding daily order cap, an optional pre-market-checklist
gate, a per-binding risk % and lot cap, and one order per alert (idempotency key ``auto:<alert id>``). Nothing is
ever retried silently: a block or a failure is recorded on the alert and the binding and (optionally) notified.

This is the only module besides ``api/execution.py`` allowed to import ``services.execution`` (invariant test).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..models import (
    AccountSettings,
    Execution,
    Market,
    Strategy,
    StrategyAlert,
    StrategyBinding,
    TradingAccount,
)
from ..schemas import TargetIn, TradeIn
from . import accounts, checklist, execution, notify
from . import trades as trade_service
from .account import current_equity, local_now
from .i18n import N_, tr
from .sizing import SizingError, calculate_position_size

log = logging.getLogger("trading_os.auto_execute")

ORDER_TYPES = ("market", "limit")
MIN_RISK_PCT, MAX_RISK_PCT = 0.01, 20.0
MAX_DAILY_CAP = 50


@dataclass
class AutoResult:
    status: str  # executed | skipped | blocked | error
    message: str
    execution_id: int | None = None
    trade_id: int | None = None


# --------------------------------------------------------------------------- bindings


def binding_for_strategy(db: Session, strategy_id: int | None) -> StrategyBinding | None:
    if not strategy_id:
        return None
    return db.scalar(select(StrategyBinding).where(StrategyBinding.strategy_id == strategy_id).limit(1))


def _orders_today(db: Session, binding: StrategyBinding, today_start: datetime) -> int:
    """Orders this binding has already sent today (any outcome; a guardrail block creates no row)."""
    return (
        db.scalar(
            select(func.count(Execution.id)).where(
                Execution.binding_id == binding.id, Execution.created_at >= today_start
            )
        )
        or 0
    )


def _checklist_done(db: Session, today) -> bool:
    return checklist.is_complete(db, today)


def arming_problem(settings: AccountSettings, account: TradingAccount) -> str | None:
    """Why a binding cannot be armed yet (message), or None. Arming needs the account already sending orders."""
    if account.status != "active":
        return tr("This account is archived.")
    if not account.execution_enabled:
        return tr(
            "Switch order sending on for {name} first (Trading Accounts → Connect), then arm automation.",
            name=accounts.account_label(settings, account),
        )
    return None


# ------------------------------------------------------------------- building a ticket


def _ticket(db: Session, settings: AccountSettings, binding: StrategyBinding, alert: StrategyAlert, now: datetime):
    """A sized :class:`TradeIn` for the alert on the binding's account, or a reason it cannot be sized."""
    account = db.get(TradingAccount, binding.account_id)
    if account is None:
        return None, tr("The linked account no longer exists.")
    market = db.scalar(select(Market).where(Market.symbol == alert.symbol))
    if market is None:
        return None, tr("{symbol} is not one of your markets.", symbol=alert.symbol)
    currency = accounts.currency_of(settings, account)
    equity = current_equity(db, settings, (account.id,))
    try:
        result = calculate_position_size(
            market, currency, equity, binding.risk_pct, entry=alert.entry_price, stop=alert.stop_loss
        )
    except SizingError as exc:
        return None, str(exc)
    if result.lots <= 0:
        return None, tr("The position size came out as zero at {risk:g}% risk.", risk=binding.risk_pct)
    lots = result.lots
    if binding.max_lots_per_trade and lots > binding.max_lots_per_trade:
        lots = binding.max_lots_per_trade
    targets = list(alert.take_profits or [])
    close_pcts = _even_splits(len(targets))
    strategy = db.get(Strategy, binding.strategy_id)
    ticket = TradeIn(
        status="open",
        entry_time=now,
        symbol=alert.symbol,
        direction=alert.direction,
        entry_price=alert.entry_price,
        stop_loss=alert.stop_loss,
        take_profit=targets[-1] if targets else None,
        position_size=lots,
        risk_amount=None,
        strategy=strategy.name if strategy else alert.strategy,
        timeframe=alert.timeframe,
        confluences=list(alert.confluences or []),
        source="signal",
        targets=[TargetIn(price=p, close_pct=c) for p, c in zip(targets, close_pcts, strict=True)],
        account_id=account.id,
    )
    return ticket, None


def _even_splits(n: int) -> list[float]:
    if n <= 0:
        return []
    base = round(100 / n, 2)
    splits = [base] * n
    splits[-1] = round(100 - base * (n - 1), 2)
    return splits


# ------------------------------------------------------------------------- execution

SKIP_DISABLED = N_("automation off")


def execute_for_alert(
    db: Session,
    settings: AccountSettings,
    box,
    alert: StrategyAlert,
    binding: StrategyBinding,
    now: datetime | None = None,
) -> AutoResult:
    """Send the alert as an order on the binding's account, with every rail. Returns what happened."""
    now = now or local_now(settings)
    if box is None:  # no secret box in this context: never silently guess credentials
        return AutoResult("skipped", tr("Secrets are unavailable in this context."))
    if not (binding.enabled and binding.auto_execute):
        return AutoResult("skipped", tr(SKIP_DISABLED))
    account = db.get(TradingAccount, binding.account_id)
    if account is None or account.status != "active" or not account.execution_enabled:
        return _record(db, binding, alert, AutoResult("skipped", tr("Order sending is off for this account.")), now)
    if binding.require_checklist and not _checklist_done(db, now.date()):
        return _record(db, binding, alert, AutoResult("skipped", tr("The pre-market checklist is not done.")), now)
    cap = min(binding.max_trades_per_day, MAX_DAILY_CAP) if binding.max_trades_per_day else MAX_DAILY_CAP
    today_start = datetime.combine(now.date(), datetime.min.time())
    if _orders_today(db, binding, today_start) >= cap:
        return _record(db, binding, alert, AutoResult("skipped", tr("Today's auto-order limit is reached.")), now)

    ticket, reason = _ticket(db, settings, binding, alert, now)
    if ticket is None:
        return _record(db, binding, alert, AutoResult("error", reason), now)

    key = f"auto:{alert.id}"
    try:
        result = execution.submit(db, settings, box, account, ticket, key, order_type=binding.order_type)
    except trade_service.TradeBlocked as exc:
        codes = ", ".join(sorted({v.code for v in exc.violations}))
        return _record(
            db, binding, alert, AutoResult("blocked", tr("Guardrails blocked it: {codes}", codes=codes)), now
        )
    except execution.ExecutionError as exc:
        db.rollback()
        return _record(db, binding, alert, AutoResult("error", str(exc)), now)
    except Exception as exc:  # noqa: BLE001 - a bug here must never crash the scan loop
        db.rollback()
        log.exception("auto execution failed")
        return _record(db, binding, alert, AutoResult("error", f"{type(exc).__name__}"), now)

    row = result.execution
    row.binding_id = binding.id
    accepted = row.status in ("pending", "sent", "filled")
    message = row.message or (tr("Order sent") if accepted else tr("Not accepted"))
    outcome = AutoResult(
        "executed" if accepted else "error",
        message,
        execution_id=row.id,
        trade_id=result.trade.id if result.trade is not None else None,
    )
    db.commit()
    return _record(db, binding, alert, outcome, now)


def _record(
    db: Session, binding: StrategyBinding, alert: StrategyAlert, result: AutoResult, now: datetime
) -> AutoResult:
    """Write the outcome onto the binding and the alert, notify once, and return it unchanged."""
    binding.last_result = result.message[:300]
    if result.status == "executed":
        binding.last_auto_at = now
        alert.status = "taken"
        if result.trade_id is not None:
            alert.trade_id = result.trade_id
    details = dict(alert.details or {})
    details["auto"] = {
        "binding_id": binding.id,
        "status": result.status,
        "message": result.message,
        "execution_id": result.execution_id,
        "at": now.isoformat(),
    }
    alert.details = details
    db.commit()
    if binding.notify and result.status in ("executed", "blocked", "error"):
        settings = db.get(AccountSettings, 1)
        if settings is not None:
            _notify(db, settings, binding, alert, result)
    return result


TITLES = {
    "executed": N_("Auto order sent: {symbol} {direction}"),
    "blocked": N_("Auto order blocked: {symbol}"),
    "error": N_("Auto order failed: {symbol}"),
}


def _notify(
    db: Session, settings: AccountSettings, binding: StrategyBinding, alert: StrategyAlert, result: AutoResult
) -> None:
    title = tr(TITLES.get(result.status, TITLES["error"]), symbol=alert.symbol, direction=tr(alert.direction))
    priority = "high" if result.status != "executed" else "default"
    try:
        notify.send(
            db,
            settings,
            notify.Note(
                event="risk",
                title=title,
                body=result.message,
                url="#/signals",
                priority=priority,
                data={"alert_id": alert.id, "binding_id": binding.id, "auto_status": result.status},
            ),
        )
    except Exception:  # noqa: BLE001 - a notification failure never changes the order outcome
        log.warning("auto execution notification failed")


# ------------------------------------------------------------------------ scan hook


def run_for_results(db: Session, settings: AccountSettings, box, scan_results: list[dict]) -> list[dict]:
    """Called by the scanner after a scan: auto-execute each new alert whose strategy has an armed binding.

    ``scan_results`` are the rows from ``alerts.scan``; only rows with ``status == "alert"`` and an ``alert_id``
    are considered. Returns one summary dict per attempt (empty when nothing is armed)."""
    out: list[dict] = []
    now = local_now(settings)
    for row in scan_results:
        if row.get("status") != "alert" or not row.get("alert_id"):
            continue
        alert = db.get(StrategyAlert, row["alert_id"])
        if alert is None:
            continue
        binding = binding_for_strategy(db, _strategy_id(db, alert))
        if binding is None or not (binding.enabled and binding.auto_execute):
            continue
        result = execute_for_alert(db, settings, box, alert, binding, now)
        out.append(
            {
                "alert_id": alert.id,
                "binding_id": binding.id,
                "symbol": alert.symbol,
                "status": result.status,
                "message": result.message,
                "execution_id": result.execution_id,
            }
        )
    return out


def _strategy_id(db: Session, alert: StrategyAlert) -> int | None:
    if not alert.strategy:
        return None
    return db.scalar(select(Strategy.id).where(Strategy.name == alert.strategy))


# ------------------------------------------------------------------------------ output


def binding_out(db: Session, settings: AccountSettings, binding: StrategyBinding) -> dict:
    account = db.get(TradingAccount, binding.account_id)
    strategy = db.get(Strategy, binding.strategy_id)
    ready = account is not None and account.status == "active" and account.execution_enabled
    return {
        "id": binding.id,
        "strategy_id": binding.strategy_id,
        "strategy_name": strategy.name if strategy else "",
        "account_id": binding.account_id,
        "account_name": accounts.account_label(settings, account) if account else "",
        "account_connection": account.connection if account else "none",
        "account_paper": bool(account and account.connection == "paper"),
        "enabled": binding.enabled,
        "auto_execute": binding.auto_execute,
        "risk_pct": binding.risk_pct,
        "max_lots_per_trade": binding.max_lots_per_trade,
        "order_type": binding.order_type,
        "max_trades_per_day": binding.max_trades_per_day,
        "require_checklist": binding.require_checklist,
        "notify": binding.notify,
        "can_arm": ready,
        "arming_problem": arming_problem(settings, account) if account else "",
        "last_auto_at": binding.last_auto_at.isoformat() if binding.last_auto_at else None,
        "last_result": binding.last_result,
    }


# ---------------------------------------------------- Telegram signals (optional, guarded)


def _telegram_sent_today(db: Session, account_id: int, today_start: datetime) -> int:
    from ..models import TelegramSignal

    return (
        db.scalar(
            select(func.count(TelegramSignal.id)).where(
                TelegramSignal.status == "sent", TelegramSignal.received_at >= today_start
            )
        )
        or 0
    )


def execute_signal(db: Session, settings: AccountSettings, box, listener, signal, now: datetime) -> AutoResult:
    """Send a captured Telegram signal as an order on the listener's chosen account, with the same rails as a
    strategy binding. Requires a parsed signal with a symbol, direction, stop and entry price."""
    if box is None:
        return AutoResult("skipped", tr("Secrets are unavailable in this context."))
    if not (listener.enabled and listener.auto_execute and listener.auto_account_id):
        return AutoResult("skipped", tr(SKIP_DISABLED))
    if not (signal.parse_ok and signal.entry_price is not None and signal.stop_loss is not None):
        return _record_signal(db, signal, AutoResult("skipped", tr("The signal is missing an entry or stop price.")))
    account = db.get(TradingAccount, listener.auto_account_id)
    if account is None or account.status != "active" or not account.execution_enabled:
        return _record_signal(db, signal, AutoResult("skipped", tr("Order sending is off for this account.")))
    if listener.require_checklist and not checklist.is_complete(db, now.date()):
        return _record_signal(db, signal, AutoResult("skipped", tr("The pre-market checklist is not done.")))
    cap = min(listener.max_trades_per_day, MAX_DAILY_CAP) if listener.max_trades_per_day else MAX_DAILY_CAP
    today_start = datetime.combine(now.date(), datetime.min.time())
    if _telegram_sent_today(db, account.id, today_start) >= cap:
        return _record_signal(db, signal, AutoResult("skipped", tr("Today's auto-order limit is reached.")))

    market = db.scalar(select(Market).where(Market.symbol == signal.symbol))
    if market is None:
        return _record_signal(
            db, signal, AutoResult("error", tr("{symbol} is not one of your markets.", symbol=signal.symbol))
        )
    currency = accounts.currency_of(settings, account)
    equity = current_equity(db, settings, (account.id,))
    try:
        sized = calculate_position_size(
            market, currency, equity, listener.risk_pct, entry=signal.entry_price, stop=signal.stop_loss
        )
    except SizingError as exc:
        return _record_signal(db, signal, AutoResult("error", str(exc)))
    if sized.lots <= 0:
        return _record_signal(
            db,
            signal,
            AutoResult("error", tr("The position size came out as zero at {risk:g}% risk.", risk=listener.risk_pct)),
        )
    targets = list(signal.take_profits or [])
    ticket = TradeIn(
        status="open",
        entry_time=now,
        symbol=signal.symbol,
        direction=signal.direction,
        entry_price=signal.entry_price,
        stop_loss=signal.stop_loss,
        take_profit=targets[-1] if targets else None,
        position_size=sized.lots,
        risk_amount=None,
        source="signal",
        targets=[TargetIn(price=p, close_pct=c) for p, c in zip(targets, _even_splits(len(targets)), strict=True)],
        account_id=account.id,
    )
    key = f"tg:{signal.id}"
    try:
        result = execution.submit(db, settings, box, account, ticket, key, order_type="market")
    except trade_service.TradeBlocked as exc:
        codes = ", ".join(sorted({v.code for v in exc.violations}))
        return _record_signal(db, signal, AutoResult("blocked", tr("Guardrails blocked it: {codes}", codes=codes)))
    except execution.ExecutionError as exc:
        db.rollback()
        return _record_signal(db, signal, AutoResult("error", str(exc)))
    except Exception as exc:  # noqa: BLE001
        db.rollback()
        log.exception("telegram auto execution failed")
        return _record_signal(db, signal, AutoResult("error", f"{type(exc).__name__}"))
    row = result.execution
    accepted = row.status in ("pending", "sent", "filled")
    outcome = AutoResult(
        "executed" if accepted else "error",
        row.message or tr("Order sent"),
        execution_id=row.id,
        trade_id=result.trade.id if result.trade is not None else None,
    )
    db.commit()
    return _record_signal(db, signal, outcome)


def _record_signal(db: Session, signal, result: AutoResult):
    signal.auto_result = result.message[:300]
    if result.status == "executed":
        signal.status = "sent"
        signal.execution_id = result.execution_id
    db.commit()
    return result


def run_for_telegram(db: Session, settings: AccountSettings, box, signals: list) -> list[dict]:
    """Called by the Telegram listener after it captures signals: auto-execute when the listener is armed."""
    from ..models import TelegramListener

    listener = db.get(TelegramListener, 1)
    if listener is None or not (listener.enabled and listener.auto_execute):
        return []
    now = local_now(settings)
    out = []
    for signal in signals:
        if signal.status != "new" or not signal.parse_ok:
            continue
        result = execute_signal(db, settings, box, listener, signal, now)
        out.append(
            {"signal_id": signal.id, "symbol": signal.symbol, "status": result.status, "message": result.message}
        )
    return out
