"""Webhook bridge: POST each order as JSON to the trader's own bridge (a copier, a VPS script, a broker relay).

Body: ``{"event": "order", "order": {...}, "account": {...}, "sent_at": ...}``. With a ``webhook_secret``
credential the raw body is signed: header ``X-TradingOS-Signature: sha256=<hex HMAC-SHA256>``, the same scheme
as notification webhooks. A 2xx answer means "sent"; a JSON answer may add ``ticket`` / ``order_id`` (the
broker reference), ``fill_price`` (filled) or ``status`` (filled | pending | rejected | error) and ``message``.
A 4xx answer is a rejection, a 5xx answer or a network failure an error.
"""

from __future__ import annotations

from datetime import datetime, timezone
from urllib.parse import urlsplit

import httpx

from ..i18n import tr
from . import base
from .base import Bridge, BridgeConfigError, BridgeContext, BridgeResult, Order, TestResult


class WebhookBridge(Bridge):
    kind = "webhook"
    secrets = ("webhook_secret",)
    destination_keys = ("url",)

    def _validate(self, config: dict) -> dict:
        url = base.text(config, "url", 500)
        if not url:
            raise BridgeConfigError(tr("Enter the webhook URL"))
        return {"url": base.checked_url(url, label=tr("The webhook URL"))}

    def missing(self, ctx: BridgeContext) -> str | None:
        return None if ctx.config.get("url") else tr("Enter the webhook URL first")

    def _account(self, ctx: BridgeContext) -> dict:
        return {"id": ctx.account_id, "name": ctx.account_name, "login": ctx.login, "server": ctx.server}

    def _post(self, ctx: BridgeContext, payload: dict, headers: dict[str, str] | None = None) -> httpx.Response:
        body = base.json_body(payload)
        headers = {"Content-Type": "application/json", **(headers or {})}
        secret = ctx.secret("webhook_secret")
        if secret:
            headers["X-TradingOS-Signature"] = base.signature(secret, body)
        return base._http_post(ctx.config["url"], body, headers)

    def submit(self, ctx: BridgeContext, order: Order) -> BridgeResult:
        url = ctx.config.get("url", "")
        host = urlsplit(url).hostname or url
        payload = {
            "event": "order",
            "order": order.to_dict(),
            "account": self._account(ctx),
            "sent_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        }
        try:
            response = self._post(ctx, payload, {"Idempotency-Key": order.idempotency_key})
        except httpx.HTTPError as exc:
            return BridgeResult("error", message=base.transport_error(exc, host, order=True))
        data = base.response_json(response)
        details = {"http_status": response.status_code}
        if isinstance(data, dict):
            details["body"] = data
        elif response.text:
            details["text"] = response.text[:300]
        if 300 <= response.status_code < 400:
            return BridgeResult(
                "error",
                message=tr(
                    "{host} answered with a redirect (HTTP {status}); use the final address",
                    host=host,
                    status=response.status_code,
                ),
                response=details,
            )
        if response.status_code >= 400:
            reason = base.truncate(base.error_detail(data, response), 160)
            message = f"HTTP {response.status_code}" + (f": {reason}" if reason else "")
            status = "rejected" if response.status_code < 500 else "error"
            return BridgeResult(status, message=message, response=details)
        return self._result(data if isinstance(data, dict) else {}, response.status_code, details)

    def _result(self, data: dict, http_status: int, details: dict) -> BridgeResult:
        ref = ""
        for key in ("ticket", "order_id", "orderId"):
            if data.get(key) not in (None, ""):
                ref = str(data[key])[:64]
                break
        fill = base.number(data.get("fill_price"))
        status = str(data.get("status") or "").lower()
        note = data.get("message") or data.get("error") or data.get("reason") or ""
        note = base.truncate(note, 200) if isinstance(note, str) else ""
        if status in ("rejected", "error"):
            return BridgeResult(status, ref, None, note or tr("The webhook refused the order"), details)
        if fill is not None or status == "filled":
            message = tr("Filled at {price:g}", price=fill) if fill is not None else tr("Filled")
            return BridgeResult("filled", ref, fill, message, details)
        if status == "pending":
            return BridgeResult("pending", ref, None, note or tr("Accepted as a pending order"), details)
        return BridgeResult(
            "sent", ref, None, note or tr("Delivered to the webhook (HTTP {status})", status=http_status), details
        )

    def test(self, ctx: BridgeContext) -> TestResult:
        url = ctx.config.get("url", "")
        if not url:
            return TestResult(False, tr("Enter the webhook URL first"))
        host = urlsplit(url).hostname or url
        payload = {
            "event": "test",
            "account": self._account(ctx),
            "sent_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        }
        try:
            response = self._post(ctx, payload)
        except httpx.HTTPError as exc:
            return TestResult(False, base.transport_error(exc, host, order=False))
        if 200 <= response.status_code < 300:
            return TestResult(True, tr("The webhook answered (HTTP {status})", status=response.status_code))
        reason = base.truncate(base.error_detail(base.response_json(response), response), 160)
        return TestResult(False, f"HTTP {response.status_code}" + (f": {reason}" if reason else ""))
