"""OANDA v20 REST API (practice or live account).

Uses the ``api_token`` credential and ``connection_config`` ``{account_id, environment: practice|live}``.
Market orders are fill-or-kill, limit and stop orders good-till-cancelled; stop loss and take profit ride
along as ``stopLossOnFill`` / ``takeProfitOnFill``. Units = lots x the market's contract size, negative for
a sell. Symbols map to OANDA instruments: ``symbol_map`` first, then the market's base/quote currencies
(XAUUSD -> XAU_USD, EURUSD -> EUR_USD), then a few common index names.
"""

from __future__ import annotations

import math
import re

import httpx

from ..i18n import tr
from . import base
from .base import Bridge, BridgeConfigError, BridgeContext, BridgeResult, BridgeUnavailable, Order, TestResult

HOSTS = {"practice": "https://api-fxpractice.oanda.com", "live": "https://api-fxtrade.oanda.com"}
ACCOUNT_RE = re.compile(r"^[0-9A-Za-z-]{5,40}$")
INSTRUMENT_RE = re.compile(r"^[A-Z0-9]{2,10}_[A-Z0-9]{2,10}$")
COMMON_INSTRUMENTS = {
    "NAS100": "NAS100_USD",
    "US100": "NAS100_USD",
    "US30": "US30_USD",
    "SPX500": "SPX500_USD",
    "US500": "SPX500_USD",
    "UK100": "UK100_GBP",
    "GER40": "DE30_EUR",
    "DE40": "DE30_EUR",
    "JP225": "JP225_USD",
    "USOIL": "WTICO_USD",
    "UKOIL": "BCO_USD",
}


class OandaBridge(Bridge):
    kind = "oanda"
    secrets = ("api_token",)
    destination_keys = ("account_id", "environment")
    cancellable = True

    def _validate(self, config: dict) -> dict:
        account_id = base.text(config, "account_id", 40)
        if account_id and not ACCOUNT_RE.match(account_id):
            raise BridgeConfigError(tr("The OANDA account ID looks like 101-004-1234567-001"))
        environment = base.text(config, "environment", 10) or "practice"
        if environment not in HOSTS:
            raise BridgeConfigError(tr("The OANDA environment is practice or live"))
        return {"account_id": account_id, "environment": environment}

    def missing(self, ctx: BridgeContext) -> str | None:
        if not ctx.config.get("account_id"):
            return tr("Enter the OANDA account ID")
        if not ctx.secret("api_token"):
            return tr("Enter the OANDA API token")
        return None

    def broker_symbol(self, symbol: str, config: dict, base_currency: str | None = None, quote: str = "") -> str:
        mapped = (config.get("symbol_map") or {}).get(symbol)
        if mapped:
            return mapped
        if base_currency and quote and symbol == f"{base_currency}{quote}":
            return f"{base_currency}_{quote}"
        if symbol in COMMON_INSTRUMENTS:
            return COMMON_INSTRUMENTS[symbol]
        if len(symbol) == 6 and symbol.isalpha():
            return f"{symbol[:3]}_{symbol[3:]}"
        return symbol

    # ------------------------------------------------------------- HTTP

    def _url(self, ctx: BridgeContext, path: str) -> str:
        host = HOSTS.get(ctx.config.get("environment") or "practice", HOSTS["practice"])
        return f"{host}/v3/accounts/{ctx.config.get('account_id', '')}{path}"

    def _call(self, ctx: BridgeContext, method: str, path: str, payload: dict | None = None) -> httpx.Response:
        headers = {
            "Authorization": f"Bearer {ctx.secret('api_token')}",
            "Accept-Datetime-Format": "RFC3339",
        }
        body = None
        if payload is not None:
            headers["Content-Type"] = "application/json"
            body = base.json_body(payload)
        return base._http_request(method, self._url(ctx, path), headers, body)

    @staticmethod
    def _price(value: float, decimals: int | None) -> str:
        return f"{value:.{decimals if decimals is not None else 5}f}"

    # ------------------------------------------------------------- orders

    def units(self, order: Order) -> int:
        size = order.contract_size or 1.0
        units = int(round(order.lots * size))
        return units if order.side == "buy" else -units

    def submit(self, ctx: BridgeContext, order: Order) -> BridgeResult:
        instrument = order.broker_symbol
        if not INSTRUMENT_RE.match(instrument):
            return BridgeResult(
                "rejected",
                message=tr(
                    "{symbol} is not an OANDA instrument - add it to the symbol map (e.g. XAU_USD)", symbol=instrument
                ),
            )
        units = self.units(order)
        if units == 0:
            return BridgeResult("rejected", message=tr("{lots:g} lots is less than one OANDA unit", lots=order.lots))
        spec: dict = {"instrument": instrument, "units": str(units), "positionFill": "DEFAULT"}
        if order.order_type == "market":
            spec.update(type="MARKET", timeInForce="FOK")
        else:
            if not order.price:
                return BridgeResult("rejected", message=tr("A limit or stop order needs a price"))
            spec.update(
                type=order.order_type.upper(), price=self._price(order.price, order.price_decimals), timeInForce="GTC"
            )
        if order.stop_loss:
            spec["stopLossOnFill"] = {"price": self._price(order.stop_loss, order.price_decimals)}
        if order.take_profit:
            spec["takeProfitOnFill"] = {"price": self._price(order.take_profit, order.price_decimals)}
        spec["clientExtensions"] = {"id": f"tos-{order.id}", "comment": (order.comment or "Trading OS")[:100]}
        try:
            response = self._call(ctx, "POST", "/orders", {"order": spec})
        except httpx.HTTPError as exc:
            return BridgeResult("error", message=base.transport_error(exc, "OANDA", order=True))
        data = base.response_json(response)
        data = data if isinstance(data, dict) else {}
        details = {"http_status": response.status_code, "request": {"order": spec}, "body": data}
        if response.status_code >= 400:
            reject = data.get("orderRejectTransaction") or {}
            reason = data.get("errorMessage") or reject.get("rejectReason") or response.reason_phrase
            status = "rejected" if response.status_code < 500 else "error"
            return BridgeResult(
                status,
                message=tr("OANDA refused the order: {reason}", reason=base.truncate(str(reason), 160)),
                response=details,
            )
        fill = data.get("orderFillTransaction") or {}
        cancel = data.get("orderCancelTransaction") or {}
        create = data.get("orderCreateTransaction") or {}
        if fill:
            price = base.number(fill.get("price"))
            ref = str((fill.get("tradeOpened") or {}).get("tradeID") or fill.get("id") or create.get("id") or "")
            message = tr("Filled at {price:g} (ticket {ticket})", price=price, ticket=ref) if price else tr("Filled")
            return BridgeResult("filled", ref, price, message, details)
        if cancel:
            reason = cancel.get("reason") or "CANCELLED"
            return BridgeResult(
                "rejected",
                str(create.get("id") or ""),
                message=tr("OANDA cancelled the order: {reason}", reason=reason),
                response=details,
            )
        ref = str(create.get("id") or "")
        return BridgeResult("pending", ref, None, tr("Pending order placed (ticket {ticket})", ticket=ref), details)

    def cancel(self, ctx: BridgeContext, order: Order, broker_ref: str) -> BridgeResult:
        if not broker_ref:
            return super().cancel(ctx, order, broker_ref)
        try:
            response = self._call(ctx, "PUT", f"/orders/{broker_ref}/cancel")
        except httpx.HTTPError as exc:
            return BridgeResult("error", broker_ref, message=base.transport_error(exc, "OANDA", order=False))
        if response.status_code < 300:
            return BridgeResult("cancelled", broker_ref, message=tr("Cancelled at OANDA"))
        reason = base.error_detail(base.response_json(response), response)
        return BridgeResult("error", broker_ref, message=tr("OANDA did not cancel the order: {reason}", reason=reason))

    def snapshot(self, ctx: BridgeContext) -> dict | None:
        problem = self.missing(ctx)
        if problem:
            raise BridgeUnavailable(problem)
        try:
            response = self._call(ctx, "GET", "/summary")
        except httpx.HTTPError as exc:
            raise BridgeUnavailable(base.transport_error(exc, "OANDA", order=False)) from exc
        data = base.response_json(response)
        if response.status_code >= 400 or not isinstance(data, dict) or not isinstance(data.get("account"), dict):
            reason = base.error_detail(data, response)
            raise BridgeUnavailable(
                tr("OANDA answered HTTP {status}: {reason}", status=response.status_code, reason=reason)
            )
        account = data["account"]
        margin = account.get("marginUsed")
        equity = account.get("NAV")
        level = None
        try:
            if float(margin) > 0 and not math.isnan(float(equity)):
                level = float(equity) / float(margin) * 100
        except (TypeError, ValueError):
            level = None
        return base.snapshot_from(
            balance=account.get("balance"),
            equity=equity,
            floating_pnl=account.get("unrealizedPL"),
            margin=margin,
            free_margin=account.get("marginAvailable"),
            margin_level=level,
            open_positions=account.get("openTradeCount"),
        )

    def test(self, ctx: BridgeContext) -> TestResult:
        try:
            snapshot = self.snapshot(ctx)
        except BridgeUnavailable as exc:
            return TestResult(False, str(exc))
        environment = ctx.config.get("environment") or "practice"
        return TestResult(
            True,
            tr(
                "Connected to OANDA {environment} account {account}",
                environment=environment,
                account=ctx.config.get("account_id", ""),
            ),
            snapshot,
        )
