"""Live signal execution: send a trade to a trading account through its bridge and log it in the journal.

Safety rails, in the order :func:`submit` applies them:

1. **Idempotency.** An order key is used once: the same key returns the stored Execution and never sends a
   second order (a double click, a retried request, two tabs).
2. **Switched on, on purpose.** The account must be active, connected and have order sending switched on
   (``execution_enabled``). Anything but paper trading is switched on only by typing the account's exact
   name; changing the connection type (or where it sends to) switches it off again. ``max_lots_per_order``
   caps every order.
3. **The same guardrails as the journal.** The order is judged by ``services.trades.evaluate`` on its own
   account, exactly like logging the trade: a blocking violation (kill switch, daily loss limit, checklist
   gate, prop-firm rules ...) raises ``TradeBlocked`` (HTTP 423) and NOTHING is sent or stored.
4. **Journal and order stay linked.** A sent / filled / pending order becomes a journal trade (source
   "signal", on that account, entry at the fill price when known) linked by ``Execution.trade_id``; a
   rejected or failed order keeps only its Execution row and message.
5. **No secrets at rest.** ``Execution.request`` / ``response`` never hold a token, password or signature.

Orders are only ever sent by an explicit request of the trader (``POST /api/execution/orders``). Strategy
alerts and background jobs never call this module.

**A pending order that never fills** (rejected by the terminal, cancelled, expired in the queue) never
existed at the broker, so its journal trade is *removed* when it is untouched (still open, no fills, no
screenshots): equity, the daily trade count and the statistics stay true, and the Execution row keeps the
record (``response.removed_trade_id``). A trade the trader already worked on is kept, tagged
``order-rejected`` with a note, for them to decide.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..models import AccountSettings, Execution, Market, Trade, TradingAccount
from ..schemas import TradeIn
from . import accounts, positions, secrets_store
from . import trades as trade_service
from .account import local_now
from .bridges import (
    BRIDGES,
    Bridge,
    BridgeContext,
    BridgeResult,
    BridgeUnavailable,
    Order,
    mt_bridge,
    sanitize,
    truncate,
)
from .bridges.base import BROKER_SYMBOL_RE, JOURNAL_SYMBOL_RE, ORDER_TYPES, RESULT_STATUSES
from .guardrails import Violation
from .i18n import tr
from .sizing import blended_rr, planned_rr, risk_for_lots

log = logging.getLogger("trading_os.execution")

STATUSES = ("pending", "sent", "filled", "rejected", "error", "cancelled")
INTERRUPTED_AFTER = timedelta(minutes=2)  # a send that never finished (the app stopped mid-call)
REJECTED_TAG = "order-rejected"
LOG_LIMIT = 200


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _age(execution: Execution, key: str = "created_utc") -> timedelta | None:
    """Real time since ``request[key]`` (UTC): immune to time-zone changes and daylight-saving shifts."""
    stamp = (execution.request or {}).get(key) or (execution.response or {}).get(key)
    if not stamp:
        return None
    try:
        return _utc_now() - datetime.fromisoformat(stamp)
    except ValueError:
        return None


def destination(account: TradingAccount) -> dict:
    """Where the account's orders go (no secrets): an order is cancelled only where it was sent."""
    bridge = BRIDGES.get(account.connection)
    config = account.connection_config or {}
    keys = bridge.destination_keys if bridge is not None else ()
    return {
        "connection": account.connection,
        **{key: config.get(key) for key in keys},
        "login": account.login,
        "server": account.server,
    }


class ExecutionError(Exception):
    """An order or a connection change Trading OS refuses (``status_code`` for the API)."""

    def __init__(self, message: str, status_code: int = 409, code: str = ""):
        super().__init__(message)
        self.status_code = status_code
        self.code = code


@dataclass
class SubmitResult:
    execution: Execution
    trade: Trade | None = None
    violations: list[Violation] = field(default_factory=list)
    duplicate: bool = False


# --------------------------------------------------------------------------- account helpers


def account_name(settings: AccountSettings, account: TradingAccount) -> str:
    return settings.account_name if accounts.is_default(settings, account) else account.name


def account_currency(settings: AccountSettings, account: TradingAccount | None) -> str:
    if account is None or accounts.is_default(settings, account):
        return settings.currency
    return account.currency


def bridge_context(db: Session, settings: AccountSettings, box, account: TradingAccount) -> BridgeContext:
    """The bridge's view of ``account``, with the secrets it needs decrypted (never log the result)."""
    bridge = BRIDGES.get(account.connection)
    values = {}
    for kind in bridge.secrets if bridge else ():
        try:
            values[kind] = secrets_store.get(db, box, account.id, kind)
        except secrets_store.SecretError as exc:
            raise ExecutionError(
                tr("A stored secret of this account cannot be read on this computer - enter it again"),
                409,
                "secret_unreadable",
            ) from exc
    return BridgeContext(
        account_id=account.id,
        account_name=account_name(settings, account),
        login=account.login or "",
        server=account.server or "",
        currency=account_currency(settings, account),
        config=dict(account.connection_config or {}),
        secrets=values,
        last_seen_at=account.last_seen_at,
        now=local_now(settings),
    )


def _market(db: Session, symbol: str) -> Market | None:
    return db.scalar(select(Market).where(Market.symbol == symbol))


def _price_decimals(market: Market | None) -> int | None:
    if market is None or not market.pip_size or market.pip_size <= 0:
        return None
    return max(0, min(8, -math.floor(math.log10(market.pip_size)) + 1))


def _order(execution: Execution, market: Market | None) -> Order:
    return Order(
        id=execution.id,
        idempotency_key=execution.idempotency_key,
        symbol=execution.symbol,
        broker_symbol=execution.broker_symbol or execution.symbol,
        direction=execution.direction,
        order_type=execution.order_type,
        lots=execution.lots,
        price=execution.price,
        stop_loss=execution.stop_loss,
        take_profit=execution.take_profit,
        contract_size=market.contract_size if market else None,
        price_decimals=_price_decimals(market),
        comment=f"Trading OS #{execution.id}",
    )


def _ready(db: Session, settings: AccountSettings, box, account: TradingAccount) -> tuple[Bridge, BridgeContext]:
    """The account's bridge and context when it may send orders now; otherwise :class:`ExecutionError`."""
    name = account_name(settings, account)
    if account.status != "active":
        raise ExecutionError(
            tr("The account '{name}' is archived: reactivate it to send orders", name=name), 409, "archived"
        )
    bridge = BRIDGES.get(account.connection)
    if bridge is None:
        raise ExecutionError(
            tr("{name} is not connected - choose a connection under Trading Accounts first", name=name),
            409,
            "not_connected",
        )
    if not account.execution_enabled:
        raise ExecutionError(
            tr("Order sending is off for {name} - turn it on under Trading Accounts first", name=name),
            409,
            "execution_disabled",
        )
    ctx = bridge_context(db, settings, box, account)
    problem = bridge.missing(ctx)
    if problem:
        raise ExecutionError(problem, 409, "not_ready")
    return bridge, ctx


def _now(settings: AccountSettings) -> datetime:
    return local_now(settings)


# --------------------------------------------------------------------------- sending an order


def _by_key(db: Session, key: str) -> Execution | None:
    return db.scalar(select(Execution).where(Execution.idempotency_key == key))


def _duplicate(db: Session, existing: Execution, account: TradingAccount) -> SubmitResult:
    if existing.account_id != account.id:
        raise ExecutionError(tr("This order key was already used for another account"), 409, "key_reused")
    trade = trade_service.get_trade(db, existing.trade_id) if existing.trade_id else None
    return SubmitResult(existing, trade, [], duplicate=True)


def submit(
    db: Session,
    settings: AccountSettings,
    box,
    account: TradingAccount,
    draft: TradeIn,
    idempotency_key: str,
    *,
    order_type: str = "market",
) -> SubmitResult:
    """Send ``draft`` (a journal trade) as an order on ``account`` - see the module docstring for the rails."""
    existing = _by_key(db, idempotency_key)
    if existing is not None:
        return _duplicate(db, existing, account)
    bridge, ctx = _ready(db, settings, box, account)
    if order_type not in ORDER_TYPES:
        raise ExecutionError(tr("Unknown order type {order_type}", order_type=order_type), 422, "order_type")
    lots = draft.position_size
    if not lots:
        raise ExecutionError(tr("Set the position size (lots) to send an order"), 422, "no_size")
    if account.max_lots_per_order and lots > account.max_lots_per_order + 1e-9:
        raise ExecutionError(
            tr(
                "{lots:g} lots is above the limit of {limit:g} lots per order set for {name}",
                lots=lots,
                limit=account.max_lots_per_order,
                name=account_name(settings, account),
            ),
            422,
            "max_lots",
        )

    if not JOURNAL_SYMBOL_RE.match(draft.symbol or ""):
        raise ExecutionError(tr("{symbol} is not a valid symbol", symbol=(draft.symbol or "")[:40]), 422, "symbol")
    now = _now(settings)
    market = _market(db, draft.symbol)
    data = draft.model_copy(
        update={
            "account_id": account.id,
            "source": "signal",
            "status": "open",
            "entry_time": now,
            "exit_time": None,
            "exit_price": None,
            "pnl": None,
            # The risk the guardrails judge comes from the lots and the stop, never from the client.
            "risk_amount": None if market is not None else draft.risk_amount,
        }
    )
    # The very same evaluation as POST /api/trades: a block means nothing is sent (raises TradeError too).
    evaluation = trade_service.evaluate(db, settings, data)
    if evaluation.blocked:
        raise trade_service.TradeBlocked(evaluation.violations)

    broker_symbol = bridge.broker_symbol(
        data.symbol, ctx.config, market.base_currency if market else None, market.quote_currency if market else ""
    )
    if not BROKER_SYMBOL_RE.match(broker_symbol or ""):
        raise ExecutionError(
            tr("{symbol} is not a valid broker symbol - check the symbol map", symbol=(broker_symbol or "")[:40]),
            422,
            "symbol",
        )
    take_profit = data.take_profit
    if take_profit is None and data.targets:
        take_profit = data.targets[-1].price
    execution = Execution(
        account_id=account.id,
        idempotency_key=idempotency_key,
        bridge=account.connection,
        symbol=data.symbol,
        broker_symbol=broker_symbol[:40],
        direction=data.direction,
        order_type=order_type,
        lots=lots,
        price=data.entry_price,
        stop_loss=data.stop_loss,
        take_profit=take_profit,
        status="pending",
        message=tr("Sending…"),
        request={},
        response={},
        created_at=now,
        updated_at=now,
    )
    db.add(execution)
    try:
        db.flush()  # claims the key (unique) and gives the order its id
        order = _order(execution, market)
        execution.request = sanitize(
            {
                "order": order.to_dict(),
                "trade": data.model_dump(mode="json"),
                "destination": destination(account),
                "created_utc": _utc_now().isoformat(),
            },
            ctx.secret_values(),
        )
        db.commit()
    except IntegrityError:
        db.rollback()  # the same key arrived twice at once: the first request sends, this one reports it
        existing = _by_key(db, idempotency_key)
        if existing is None:
            raise
        return _duplicate(db, existing, account)

    try:
        result = bridge.submit(ctx, order)
    except BridgeUnavailable as exc:
        result = BridgeResult("error", message=str(exc))
    except Exception as exc:  # noqa: BLE001 - a bridge bug must never leave the order in limbo
        log.warning("order bridge %s failed: %s", account.connection, type(exc).__name__)
        result = BridgeResult(
            "error",
            message=tr(
                "The connection failed unexpectedly ({error}) - check your platform before sending again",
                error=type(exc).__name__,
            ),
        )
    _apply_result(settings, execution, result, ctx.secret_values())
    account.last_error = "" if result.accepted else truncate(result.message)
    trade = None
    if result.accepted and result.filled_lots and result.filled_lots < lots:
        # Partially filled: the journal holds what really reached the account.
        data = data.model_copy(update={"position_size": result.filled_lots, "risk_amount": None})
        execution.message = truncate(
            f"{execution.message} · "
            + tr("partially: {filled:g} of {lots:g} lots", filled=result.filled_lots, lots=lots)
        )
    if result.accepted:
        trade = _record_trade(db, settings, execution, data, fill_price=result.fill_price)
    db.commit()
    return SubmitResult(execution, trade, evaluation.violations)


def _apply_result(settings: AccountSettings, execution: Execution, result: BridgeResult, secrets: tuple) -> None:
    status = result.status if result.status in RESULT_STATUSES else "error"
    execution.status = status
    execution.broker_ref = truncate(result.broker_ref or "", 64)
    execution.fill_price = result.fill_price
    execution.message = truncate(result.message or status)
    execution.response = sanitize({**(result.response or {}), "completed": True}, secrets)
    execution.updated_at = _now(settings)


def _record_trade(
    db: Session,
    settings: AccountSettings,
    execution: Execution,
    data: TradeIn,
    *,
    fill_price: float | None = None,
    when: datetime | None = None,
) -> Trade | None:
    """Log the journal trade of an order that reached the account and link it to the Execution."""
    try:
        trade, _violations = trade_service.create_trade(db, settings, data)
    except trade_service.TradeBlocked:
        # The order is already at the broker: the journal must show it even if a rule changed in between.
        trade = _force_trade(db, settings, data)
    except trade_service.TradeError as exc:
        execution.message = truncate(
            f"{execution.message} - " + tr("the journal trade could not be logged: {error}", error=str(exc))
        )
        db.commit()
        return None
    execution.trade_id = trade.id
    if fill_price is not None and (abs(fill_price - trade.entry_price) > 1e-12 or when is not None):
        apply_fill(db, settings, trade, fill_price, when)
    db.commit()
    return trade_service.get_trade(db, trade.id)


def _force_trade(db: Session, settings: AccountSettings, data: TradeIn) -> Trade:
    evaluation = trade_service.evaluate(db, settings, data)
    trade = Trade()
    trade_service._apply(trade, data, evaluation, settings)  # noqa: SLF001 - logged as-is, never blocked
    db.add(trade)
    db.commit()
    return trade_service.get_trade(db, trade.id)


def apply_fill(
    db: Session, settings: AccountSettings, trade: Trade, price: float, when: datetime | None = None
) -> None:
    """Move a journal trade's entry to the broker's fill (price, and time when given); risk and R:R follow."""
    market = _market(db, trade.symbol)
    account = db.get(TradingAccount, trade.account_id) if trade.account_id else None
    trade.entry_price = price
    if when is not None and not trade.events:
        trade.entry_time = when
    if market is not None and trade.position_size:
        risk = risk_for_lots(market, account_currency(settings, account), price, trade.stop_loss, trade.position_size)
        if risk is not None:
            trade.risk_amount = round(risk, 2)
    targets = [(target.price, target.close_pct) for target in trade.targets]
    rr = (
        blended_rr(price, trade.stop_loss, targets)
        if targets
        else planned_rr(price, trade.stop_loss, trade.take_profit)
    )
    trade.planned_rr = round(rr, 4) if rr is not None else None
    try:
        positions.recompute(trade, market, settings)
    except positions.PositionError:
        log.info("trade %s: fill applied, position fields left as they were", trade.id)


def _release_trade(db: Session, execution: Execution) -> None:
    """The order never filled: remove its untouched journal trade, or tag a trade the trader worked on."""
    if execution.trade_id is None:
        return
    trade = trade_service.get_trade(db, execution.trade_id)
    if trade is None:
        execution.trade_id = None
        return
    if trade.status == "open" and not trade.events and not trade.screenshots:
        execution.response = {**(execution.response or {}), "removed_trade_id": trade.id}
        execution.trade_id = None
        db.delete(trade)
        execution.message = truncate(f"{execution.message} " + tr("The journal trade was removed."))
        return
    tags = list(trade.tags or [])
    if REJECTED_TAG not in tags:
        trade.tags = [*tags, REJECTED_TAG]
    note = tr("The order behind this trade did not go through: {reason}", reason=execution.message)
    trade.notes = f"{trade.notes}\n{note}" if trade.notes else note


# --------------------------------------------------------------------------- pending orders


def cancel(db: Session, settings: AccountSettings, box, execution: Execution) -> Execution:
    """Cancel a pending order (paper, an MT order still waiting for the EA, a resting OANDA / MT5 order)."""
    bridge = BRIDGES.get(execution.bridge)
    response = execution.response or {}
    delivered = execution.bridge == "mt_bridge" and bool(response.get("delivered_at"))
    if delivered and execution.status == "sent" and response.get("unconfirmed"):
        # No report from MetaTrader within the order lifetime: the trader checked the terminal and releases it.
        claimed = db.execute(
            update(Execution)
            .where(Execution.id == execution.id, Execution.status == "sent")
            .values(status="cancelled", updated_at=_now(settings))
        ).rowcount
        if not claimed:
            db.rollback()
            raise ExecutionError(tr("This order changed meanwhile - refresh the log"), 409, "changed")
        db.refresh(execution)
        execution.message = tr("Released: marked as not placed (MetaTrader never reported it)")
        _release_trade(db, execution)
        db.commit()
        return execution
    if delivered and execution.status in ("pending", "sent"):
        raise ExecutionError(
            tr("The MetaTrader terminal already has this order - cancel it in MetaTrader"), 409, "delivered"
        )
    if execution.status != "pending":
        raise ExecutionError(tr("Only a pending order can be cancelled"), 409, "not_pending")
    if bridge is None or not bridge.cancellable:
        raise ExecutionError(
            tr("Orders sent this way cannot be cancelled from Trading OS - cancel it on your platform"),
            409,
            "not_cancellable",
        )
    account = db.get(TradingAccount, execution.account_id)
    sent_to = (execution.request or {}).get("destination")
    if execution.bridge in ("oanda", "mt5_terminal") and sent_to and sent_to != destination(account):
        raise ExecutionError(
            tr(
                "This order was sent to another destination than the account's current connection - cancel it on your platform"
            ),
            409,
            "destination_changed",
        )
    if execution.bridge in ("paper", "mt_bridge"):
        # Claim it atomically: the EA's poll may be handing it out at this very moment.
        claimed = db.execute(
            update(Execution)
            .where(Execution.id == execution.id, Execution.status == "pending")
            .values(status="cancelled", updated_at=_now(settings))
        ).rowcount
        if not claimed:
            db.rollback()
            raise ExecutionError(
                tr("The MetaTrader terminal already has this order - cancel it in MetaTrader"), 409, "delivered"
            )
        db.refresh(execution)
        result = BridgeResult("cancelled", execution.broker_ref, message=tr("Cancelled in Trading OS"))
    else:
        ctx = bridge_context(db, settings, box, account)
        try:
            result = bridge.cancel(ctx, _order(execution, _market(db, execution.symbol)), execution.broker_ref)
        except BridgeUnavailable as exc:
            result = BridgeResult("error", message=str(exc))
    if result.status != "cancelled":
        raise ExecutionError(result.message, 502, "cancel_failed")
    execution.status = "cancelled"
    execution.message = truncate(result.message)
    execution.updated_at = _now(settings)
    _release_trade(db, execution)
    db.commit()
    return execution


def cancel_queued(db: Session, settings: AccountSettings, account: TradingAccount, message: str) -> int:
    """Cancel the orders still waiting in Trading OS for the account's MetaTrader EA. The caller commits."""
    count = 0
    rows = db.scalars(
        select(Execution).where(
            Execution.account_id == account.id, Execution.status == "pending", Execution.bridge == "mt_bridge"
        )
    )
    for row in rows:
        if (row.response or {}).get("delivered_at"):
            continue
        row.status = "cancelled"
        row.message = truncate(message)
        row.updated_at = _now(settings)
        _release_trade(db, row)
        count += 1
    return count


def expire_stale(db: Session, settings: AccountSettings, account_id: int | None = None) -> int:
    """Expire MT orders the EA did not pick up in time; flag sends interrupted by a stop of the app."""
    now = _now(settings)
    stmt = select(Execution).where(Execution.status == "pending")
    if account_id is not None:
        stmt = stmt.where(Execution.account_id == account_id)
    changed = 0
    for row in list(db.scalars(stmt)):
        response = row.response or {}
        created = row.created_at or now
        age = _age(row)
        if age is None:  # an order stored before ages were kept in UTC
            age = now - created
        if not response.get("completed"):
            if age > INTERRUPTED_AFTER:
                row.status = "error"
                row.message = tr("Interrupted while sending - check your trading platform for this order")
                row.updated_at = now
                changed += 1
            continue
        if row.bridge == "mt_bridge" and response.get("queued") and not response.get("delivered_at"):
            account = db.get(TradingAccount, row.account_id)
            limit = mt_bridge.ttl(account.connection_config if account else {})
            if age > limit:
                row.status = "cancelled"
                row.message = tr(
                    "Expired: the MetaTrader terminal did not pick the order up within {seconds} s",
                    seconds=int(limit.total_seconds()),
                )
                row.updated_at = now
                _release_trade(db, row)
                changed += 1
    # Handed to the EA but never reported (its answer or its report got lost): say so, and let the trader
    # release the journal trade once they checked MetaTrader (cancel). It is never handed out again.
    sent = select(Execution).where(Execution.status == "sent", Execution.bridge == "mt_bridge")
    if account_id is not None:
        sent = sent.where(Execution.account_id == account_id)
    for row in list(db.scalars(sent)):
        response = row.response or {}
        if response.get("unconfirmed") or not response.get("delivered_at"):
            continue
        account = db.get(TradingAccount, row.account_id)
        limit = mt_bridge.ttl(account.connection_config if account else {})
        age = _age(row, "delivered_utc")
        if age is not None and age > limit:
            row.response = {**response, "unconfirmed": True}
            row.message = tr(
                "No report from MetaTrader within {seconds} s - check the terminal. If the order is not there, "
                "release it to remove its journal trade.",
                seconds=int(limit.total_seconds()),
            )
            row.updated_at = now
            changed += 1
    if changed:
        db.commit()
    return changed


# --------------------------------------------------------------------------- the MetaTrader EA (mt_bridge)


def account_for_token(db: Session, box, token: str) -> TradingAccount | None:
    account_id = secrets_store.find_account_by_token(db, box, token)
    return db.get(TradingAccount, account_id) if account_id is not None else None


def poll_orders(db: Session, settings: AccountSettings, account: TradingAccount) -> list[str]:
    """The EA's poll: hand out the queued orders once (they become ``sent``) as protocol lines."""
    now = _now(settings)
    account.last_seen_at = now
    expire_stale(db, settings, account.id)
    lines = []
    if account.connection == "mt_bridge" and account.execution_enabled and account.status == "active":
        rows = db.scalars(
            select(Execution)
            .where(Execution.account_id == account.id, Execution.status == "pending", Execution.bridge == "mt_bridge")
            .order_by(Execution.id)
        )
        for row in list(rows):
            response = row.response or {}
            if not response.get("queued") or response.get("delivered_at"):
                continue
            # Claim it atomically: a cancel or a second EA on the same token may race this poll.
            claimed = db.execute(
                update(Execution)
                .where(Execution.id == row.id, Execution.status == "pending")
                .values(status="sent", updated_at=now)
            ).rowcount
            if not claimed:
                continue
            db.refresh(row)
            lines.append(mt_bridge.order_line(_order(row, None)))
            row.message = tr("Delivered to the MetaTrader terminal - waiting for its report")
            row.response = {**response, "delivered_at": now.isoformat(), "delivered_utc": _utc_now().isoformat()}
    db.commit()
    return lines


def report_order(
    db: Session, settings: AccountSettings, account: TradingAccount, execution: Execution, fields: dict
) -> Execution:
    """The EA's result for one order: ``filled`` (with ticket and price), ``placed`` or ``rejected``."""
    now = _now(settings)
    account.last_seen_at = now
    status = (fields.get("status") or "").lower()
    if status not in mt_bridge.REPORT_STATUSES:
        db.commit()
        raise ExecutionError(tr("status must be filled, placed or rejected"), 400, "bad_status")
    ticket = (fields.get("ticket") or "")[:64]
    price = mt_bridge.report_price(fields)
    note = truncate(fields.get("message") or "", 200)
    current = execution.status
    report = sanitize({key: value for key, value in fields.items() if key != "token"})

    if status == "filled":
        if current == "filled":
            db.commit()
            return execution  # the same report sent twice
        fill = price or execution.price
        volume = mt_bridge.report_volume(fields)
        partial = volume is not None and 0 < volume < execution.lots - 1e-9
        execution.status = "filled"
        execution.broker_ref = ticket or execution.broker_ref
        execution.fill_price = fill
        execution.message = tr("Filled at {price:g} (ticket {ticket})", price=fill, ticket=execution.broker_ref or "-")
        if partial:
            execution.message += " · " + tr(
                "partially: {filled:g} of {lots:g} lots", filled=volume, lots=execution.lots
            )
        if note:  # e.g. the EA could not set the stop loss: the trader must see it
            execution.message = truncate(f"{execution.message} - {note}")
        execution.response = {**(execution.response or {}), "report": report}
        execution.updated_at = now
        account.last_error = truncate(note) if note else ""
        trade = trade_service.get_trade(db, execution.trade_id) if execution.trade_id else None
        if trade is not None and partial and not trade.events:
            trade.position_size = volume  # the journal holds what really filled
        if trade is not None:
            apply_fill(db, settings, trade, fill, now)
        else:  # the order filled although its journal trade is gone: the position is real, log it again
            data = _stored_trade(execution, now)
            if data is not None:
                _record_trade(db, settings, execution, data, fill_price=fill, when=now)
        db.commit()
        return execution

    if status == "rejected" and current in ("rejected", "cancelled"):
        db.commit()
        return execution  # nothing left to undo
    if status == "placed" and current == "pending" and ticket and ticket == execution.broker_ref:
        db.commit()
        return execution  # the same report sent twice
    if current not in ("pending", "sent"):
        db.commit()
        raise ExecutionError(tr("This order is already {status}", status=current), 409, "already_final")
    if status == "placed":
        execution.status = "pending"
        execution.broker_ref = ticket or execution.broker_ref
        execution.message = tr(
            "Pending order placed in MetaTrader (ticket {ticket})", ticket=execution.broker_ref or "-"
        )
        account.last_error = ""
    else:
        execution.status = "rejected"
        execution.message = note or tr("Rejected by MetaTrader")
        account.last_error = truncate(execution.message)
        _release_trade(db, execution)
    execution.response = {**(execution.response or {}), "report": report}
    execution.updated_at = now
    db.commit()
    return execution


def _stored_trade(execution: Execution, when: datetime) -> TradeIn | None:
    payload = (execution.request or {}).get("trade")
    if not isinstance(payload, dict):
        return None
    try:
        return TradeIn.model_validate({**payload, "entry_time": when.isoformat()})
    except ValueError:
        return None


def report_account(db: Session, settings: AccountSettings, account: TradingAccount, fields: dict) -> dict:
    """The EA's account figures: balance, equity, margin, free margin, floating P&L, open positions."""
    now = _now(settings)
    account.last_seen_at = now
    snapshot = mt_bridge.snapshot_from_fields(fields)
    if not snapshot:
        db.commit()
        raise ExecutionError(tr("No account figures in the report"), 400, "empty_snapshot")
    account.snapshot = snapshot
    account.snapshot_at = now
    db.commit()
    return snapshot


# --------------------------------------------------------------------------- connection settings


def set_connection(
    db: Session,
    settings: AccountSettings,
    account: TradingAccount,
    connection: str,
    config: dict,
    max_lots: float | None,
) -> bool:
    """Save how the account connects. Returns True when order sending was switched off by the change."""
    bridge = BRIDGES.get(connection)
    if connection != "none" and bridge is None:
        raise ExecutionError(tr("Unknown connection type {connection}", connection=connection), 422, "connection")
    cleaned = bridge.validate_config(config or {}) if bridge else {}
    previous = dict(account.connection_config or {})
    type_changed = connection != account.connection
    destination_changed = bridge is not None and any(
        cleaned.get(key) != previous.get(key) for key in bridge.destination_keys
    )
    switched_off = False
    if account.execution_enabled and (type_changed or destination_changed):
        account.execution_enabled = False
        switched_off = True
        cancel_queued(db, settings, account, tr("Cancelled: the connection changed"))
    if type_changed:
        if account.connection == "mt_bridge":
            cancel_queued(db, settings, account, tr("Cancelled: the connection changed"))
        account.snapshot = {}
        account.snapshot_at = None
        account.last_seen_at = None
        account.last_error = ""
    account.connection = connection
    account.connection_config = cleaned
    account.max_lots_per_order = max_lots
    db.commit()
    return switched_off


def set_execution(
    db: Session, settings: AccountSettings, box, account: TradingAccount, enabled: bool, confirm: str = ""
) -> None:
    """Switch order sending on (checked, typed confirmation unless paper) or off (always allowed)."""
    if not enabled:
        account.execution_enabled = False
        cancel_queued(db, settings, account, tr("Cancelled: order sending was switched off"))
        db.commit()
        return
    name = account_name(settings, account)
    if account.status != "active":
        raise ExecutionError(
            tr("The account '{name}' is archived: reactivate it to send orders", name=name), 409, "archived"
        )
    bridge = BRIDGES.get(account.connection)
    if bridge is None:
        raise ExecutionError(
            tr("{name} is not connected - choose a connection under Trading Accounts first", name=name),
            409,
            "not_connected",
        )
    problem = bridge.missing(bridge_context(db, settings, box, account))
    if problem:
        raise ExecutionError(problem, 409, "not_ready")
    if account.connection != "paper" and (confirm or "").strip() != name:
        raise ExecutionError(
            tr("Type the account name exactly ({name}) to send real orders from Trading OS", name=name),
            422,
            "confirm_name",
        )
    account.execution_enabled = True
    db.commit()


def test_connection(db: Session, settings: AccountSettings, box, account: TradingAccount) -> dict:
    """Run the bridge's connection test; a broker that reports the account figures updates its live metrics."""
    bridge = BRIDGES.get(account.connection)
    if bridge is None:
        raise ExecutionError(
            tr(
                "{name} is not connected - choose a connection under Trading Accounts first",
                name=account_name(settings, account),
            ),
            409,
            "not_connected",
        )
    ctx = bridge_context(db, settings, box, account)
    try:
        result = bridge.test(ctx)
    except BridgeUnavailable as exc:
        result = None
        ok, message, snapshot = False, str(exc), None
    except Exception as exc:  # noqa: BLE001 - shown to the trader, never raised
        log.warning("connection test of %s failed: %s", account.connection, type(exc).__name__)
        result = None
        ok, message, snapshot = False, tr("The connection test failed ({error})", error=type(exc).__name__), None
    if result is not None:
        ok, message, snapshot = result.ok, result.message, result.snapshot
    message = sanitize(truncate(message), ctx.secret_values())
    now = _now(settings)
    if snapshot:
        account.snapshot = snapshot
        account.snapshot_at = now
    account.last_error = "" if ok else message
    db.commit()
    return {"ok": ok, "message": message, "snapshot": snapshot or None, "tested_at": now.isoformat()}


# --------------------------------------------------------------------------- output


def execution_out(row: Execution) -> dict:
    return {
        "id": row.id,
        "account_id": row.account_id,
        "trade_id": row.trade_id,
        "binding_id": row.binding_id,  # set when this order was placed automatically by a strategy binding
        "bridge": row.bridge,
        "symbol": row.symbol,
        "broker_symbol": row.broker_symbol,
        "direction": row.direction,
        "order_type": row.order_type,
        "lots": row.lots,
        "price": row.price,
        "stop_loss": row.stop_loss,
        "take_profit": row.take_profit,
        "status": row.status,
        "broker_ref": row.broker_ref,
        "fill_price": row.fill_price,
        "message": row.message,
        "cancellable": _cancellable(row),
        "request": row.request or {},
        "response": row.response or {},
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


def _cancellable(row: Execution) -> bool:
    bridge = BRIDGES.get(row.bridge)
    if row.status != "pending" or bridge is None or not bridge.cancellable:
        return False
    return not (row.bridge == "mt_bridge" and (row.response or {}).get("delivered_at"))


def list_executions(
    db: Session, *, account_id: int | None = None, trade_id: int | None = None, limit: int = 50
) -> list[Execution]:
    stmt = select(Execution)
    if account_id is not None:
        stmt = stmt.where(Execution.account_id == account_id)
    if trade_id is not None:
        stmt = stmt.where(Execution.trade_id == trade_id)
    return list(db.scalars(stmt.order_by(Execution.id.desc()).limit(min(max(limit, 1), LOG_LIMIT))))


def connection_out(db: Session, settings: AccountSettings, box, account: TradingAccount) -> dict:
    """Everything the connection panel shows (no secret: only which ones are set, with a hint)."""
    bridge = BRIDGES.get(account.connection)
    now = _now(settings)
    try:
        missing = bridge.missing(bridge_context(db, settings, box, account)) if bridge else None
    except ExecutionError as exc:
        missing = str(exc)
    return {
        "account_id": account.id,
        "name": account_name(settings, account),
        "kind": account.kind,
        "status": account.status,
        "platform": account.platform,
        "login": account.login,
        "server": account.server,
        "connection": account.connection,
        "connection_config": dict(account.connection_config or {}),
        "execution_enabled": account.execution_enabled,
        "max_lots_per_order": account.max_lots_per_order,
        "credentials": secrets_store.described(db, account.id),
        "last_seen_at": account.last_seen_at.isoformat() if account.last_seen_at else None,
        "last_seen_seconds": mt_bridge.seen_seconds(account.last_seen_at, now),
        "last_error": account.last_error,
        "snapshot": dict(account.snapshot or {}),
        "snapshot_at": account.snapshot_at.isoformat() if account.snapshot_at else None,
        "ready": bridge is not None and missing is None,
        "missing": missing,
        "requires_confirmation": account.connection not in ("none", "paper"),
        "polling": bool(bridge and bridge.polling),
        "now": now.isoformat(),
    }
