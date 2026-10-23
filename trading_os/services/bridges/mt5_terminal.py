"""Direct MetaTrader 5 connection through the official ``MetaTrader5`` Python package (Windows only).

The package talks to a MetaTrader 5 terminal installed on the same Windows machine: ``initialize`` logs in
with the account's login (on the account record), server and password (an encrypted credential), then
``order_send`` / ``account_info`` / ``symbol_info_tick`` do the work. The package is imported lazily: on other
systems, or without it, the bridge says so clearly instead of failing at start-up. It is process-wide (one
terminal session at a time), so every call holds a lock and shuts the session down afterwards.
"""

from __future__ import annotations

import importlib
import threading
from contextlib import contextmanager

from ..i18n import N_, tr
from . import base
from .base import Bridge, BridgeContext, BridgeResult, BridgeUnavailable, Order, TestResult

MISSING_PACKAGE = N_(
    "The direct MetaTrader 5 connection is available only on Windows with MetaTrader 5 installed "
    "(pip install MetaTrader5)"
)
DONE_CODES = (10008, 10009, 10010)  # TRADE_RETCODE_PLACED, _DONE, _DONE_PARTIAL
_LOCK = threading.Lock()


def _module():
    try:
        return importlib.import_module("MetaTrader5")
    except ImportError as exc:
        raise BridgeUnavailable(tr(MISSING_PACKAGE)) from exc


def _const(mt5, name: str, default: int) -> int:
    return getattr(mt5, name, default)


def _last_error(mt5) -> str:
    try:
        code, text = mt5.last_error()
    except Exception:  # noqa: BLE001 - only used to build a message
        return ""
    return f"{text} ({code})" if text else str(code)


class Mt5TerminalBridge(Bridge):
    kind = "mt5_terminal"
    secrets = ("password",)
    cancellable = True

    def _validate(self, config: dict) -> dict:
        return {
            "magic": base.integer(config, "magic", 20250, 0, 2_147_483_647),
            "max_slippage_points": base.integer(config, "max_slippage_points", 20, 0, 10_000),
        }

    def missing(self, ctx: BridgeContext) -> str | None:
        if not ctx.login.isdigit():
            return tr("Enter the MetaTrader 5 login (account number) on the account")
        if not ctx.server:
            return tr("Enter the MetaTrader 5 server on the account")
        if not ctx.secret("password"):
            return tr("Enter the MetaTrader 5 password")
        return None

    @contextmanager
    def _session(self, ctx: BridgeContext):
        mt5 = _module()
        problem = self.missing(ctx)
        if problem:
            raise BridgeUnavailable(problem)
        with _LOCK:
            if not mt5.initialize(login=int(ctx.login), password=ctx.secret("password"), server=ctx.server):
                error = base.sanitize(_last_error(mt5), ctx.secret_values())
                raise BridgeUnavailable(tr("MetaTrader 5 did not connect: {error}", error=error))
            try:
                yield mt5
            finally:
                mt5.shutdown()

    def submit(self, ctx: BridgeContext, order: Order) -> BridgeResult:
        try:
            with self._session(ctx) as mt5:
                return self._send(ctx, mt5, order)
        except BridgeUnavailable as exc:
            return BridgeResult("error", message=str(exc))

    def _send(self, ctx: BridgeContext, mt5, order: Order) -> BridgeResult:
        symbol = order.broker_symbol
        if not mt5.symbol_select(symbol, True):
            return BridgeResult(
                "rejected",
                message=tr("{symbol} is not available on this account - check the symbol suffix or map", symbol=symbol),
            )
        buy = order.side == "buy"
        if order.order_type == "market":
            tick = mt5.symbol_info_tick(symbol)
            price = (tick.ask if buy else tick.bid) if tick is not None else None
            if not price:
                return BridgeResult(
                    "rejected", message=tr("No price for {symbol} - is the market open?", symbol=symbol)
                )
            action = _const(mt5, "TRADE_ACTION_DEAL", 1)
            kind = _const(mt5, "ORDER_TYPE_BUY", 0) if buy else _const(mt5, "ORDER_TYPE_SELL", 1)
            filling = self._filling(mt5, symbol)
        else:
            price = order.price
            action = _const(mt5, "TRADE_ACTION_PENDING", 5)
            names = {
                ("limit", True): ("ORDER_TYPE_BUY_LIMIT", 2),
                ("limit", False): ("ORDER_TYPE_SELL_LIMIT", 3),
                ("stop", True): ("ORDER_TYPE_BUY_STOP", 4),
                ("stop", False): ("ORDER_TYPE_SELL_STOP", 5),
            }
            kind = _const(mt5, *names[(order.order_type, buy)])
            filling = _const(mt5, "ORDER_FILLING_RETURN", 2)
        request = {
            "action": action,
            "symbol": symbol,
            "volume": float(order.lots),
            "type": kind,
            "price": float(price),
            "sl": float(order.stop_loss or 0.0),
            "tp": float(order.take_profit or 0.0),
            "deviation": int(ctx.config.get("max_slippage_points", 20)),
            "magic": int(ctx.config.get("magic", 20250)),
            "comment": (order.comment or "Trading OS")[:31],
            "type_time": _const(mt5, "ORDER_TIME_GTC", 0),
            "type_filling": filling,
        }
        result = mt5.order_send(request)
        if result is None:
            return BridgeResult("error", message=tr("MetaTrader 5 did not answer: {error}", error=_last_error(mt5)))
        retcode = int(getattr(result, "retcode", 0))
        details = {"retcode": retcode, "comment": str(getattr(result, "comment", ""))[:200]}
        ref = str(getattr(result, "order", "") or getattr(result, "deal", "") or "")
        if retcode not in DONE_CODES:
            reason = details["comment"] or tr("return code {code}", code=retcode)
            return BridgeResult(
                "rejected", ref, message=tr("MetaTrader 5 refused the order: {reason}", reason=reason), response=details
            )
        if order.order_type == "market":
            fill = base.number(getattr(result, "price", None)) or float(price)
            return BridgeResult(
                "filled", ref, fill, tr("Filled at {price:g} (ticket {ticket})", price=fill, ticket=ref), details
            )
        return BridgeResult("pending", ref, None, tr("Pending order placed (ticket {ticket})", ticket=ref), details)

    def _filling(self, mt5, symbol: str) -> int:
        info = mt5.symbol_info(symbol)
        mode = int(getattr(info, "filling_mode", 0) or 0)
        if mode & 1:  # SYMBOL_FILLING_FOK
            return _const(mt5, "ORDER_FILLING_FOK", 0)
        if mode & 2:  # SYMBOL_FILLING_IOC
            return _const(mt5, "ORDER_FILLING_IOC", 1)
        return _const(mt5, "ORDER_FILLING_RETURN", 2)

    def cancel(self, ctx: BridgeContext, order: Order, broker_ref: str) -> BridgeResult:
        if not broker_ref.isdigit():
            return super().cancel(ctx, order, broker_ref)
        try:
            with self._session(ctx) as mt5:
                result = mt5.order_send({"action": _const(mt5, "TRADE_ACTION_REMOVE", 8), "order": int(broker_ref)})
                retcode = int(getattr(result, "retcode", 0)) if result is not None else 0
                if retcode in DONE_CODES:
                    return BridgeResult("cancelled", broker_ref, message=tr("Cancelled in MetaTrader 5"))
                reason = str(getattr(result, "comment", "") or _last_error(mt5))
                return BridgeResult(
                    "error", broker_ref, message=tr("MetaTrader 5 did not cancel the order: {reason}", reason=reason)
                )
        except BridgeUnavailable as exc:
            return BridgeResult("error", broker_ref, message=str(exc))

    def snapshot(self, ctx: BridgeContext) -> dict | None:
        with self._session(ctx) as mt5:
            info = mt5.account_info()
            if info is None:
                raise BridgeUnavailable(tr("MetaTrader 5 did not answer: {error}", error=_last_error(mt5)))
            positions = mt5.positions_total() if hasattr(mt5, "positions_total") else None
            return base.snapshot_from(
                balance=getattr(info, "balance", None),
                equity=getattr(info, "equity", None),
                floating_pnl=getattr(info, "profit", None),
                margin=getattr(info, "margin", None),
                free_margin=getattr(info, "margin_free", None),
                margin_level=getattr(info, "margin_level", None) or None,
                open_positions=positions,
            )

    def test(self, ctx: BridgeContext) -> TestResult:
        try:
            snapshot = self.snapshot(ctx)
        except BridgeUnavailable as exc:
            return TestResult(False, str(exc))
        return TestResult(
            True,
            tr("Connected to MetaTrader 5 account {login} on {server}", login=ctx.login, server=ctx.server),
            snapshot,
        )
