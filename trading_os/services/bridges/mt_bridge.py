"""MetaTrader 4/5 bridge: an Expert Advisor in the trader's own terminal polls Trading OS for orders.

Orders are queued as ``pending``. The EA (``trading_os/bridges/TradingOS_Bridge.mq5`` / ``.mq4``) calls, with
the account's bridge token (``X-Bridge-Token`` header or ``?token=``):

* ``GET  /api/bridge/orders``       plain text, one order per line: ``id|symbol|side|type|lots|price|sl|tp``
  (``side`` buy|sell, ``type`` market|limit|stop, ``0`` = no price / stop / target). An order is handed out
  once: fetching it marks it ``sent``. Orders not picked up within ``order_ttl_sec`` expire (cancelled), so
  a terminal that comes back online hours later never fires an old signal.
* ``POST /api/bridge/orders/{id}``  ``status=filled|placed|rejected&ticket=..&price=..&message=..`` (form
  data or plain text): the result of one order.
* ``POST /api/bridge/account``      ``balance=..&equity=..&margin=..&free_margin=..&profit=..&positions=..``:
  the account figures shown as live metrics.

The text protocol needs no JSON parser in MQL. ``api/execution.py`` serves the endpoints.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from urllib.parse import parse_qsl

from ..i18n import tr
from . import base
from .base import Bridge, BridgeContext, BridgeResult, Order, TestResult

DEFAULT_TTL_SEC = 120
CONNECTED_WITHIN = timedelta(minutes=2)  # the EA polls every few seconds: older contact = not running
REPORT_STATUSES = ("filled", "placed", "rejected")
MAX_FIELD = 300


class MtBridge(Bridge):
    kind = "mt_bridge"
    secrets = ("bridge_token",)
    polling = True
    cancellable = True  # only while the order waits in Trading OS (not yet fetched by the EA)

    def _validate(self, config: dict) -> dict:
        return {"order_ttl_sec": base.integer(config, "order_ttl_sec", DEFAULT_TTL_SEC, 30, 3600)}

    def missing(self, ctx: BridgeContext) -> str | None:
        if not ctx.secret("bridge_token"):
            return tr("Create the bridge token first (on the computer running Trading OS)")
        return None

    def submit(self, ctx: BridgeContext, order: Order) -> BridgeResult:
        return BridgeResult(
            "pending",
            message=tr("Queued for the MetaTrader terminal: the Expert Advisor picks it up on its next poll"),
            response={"queued": True},
        )

    def test(self, ctx: BridgeContext) -> TestResult:
        if not ctx.secret("bridge_token"):
            return TestResult(False, tr("Create the bridge token first (on the computer running Trading OS)"))
        if ctx.last_seen_at is None or ctx.now is None:
            return TestResult(
                False, tr("No contact from the Expert Advisor yet - attach it to a chart with this token")
            )
        age = max(int((ctx.now - ctx.last_seen_at).total_seconds()), 0)
        if age <= CONNECTED_WITHIN.total_seconds():
            return TestResult(True, tr("The Expert Advisor is connected (last contact {seconds} s ago)", seconds=age))
        return TestResult(
            False,
            tr(
                "The Expert Advisor was last seen {minutes} min ago - is MetaTrader running with the EA attached?",
                minutes=age // 60,
            ),
        )


def ttl(config: dict) -> timedelta:
    try:
        seconds = int(config.get("order_ttl_sec") or DEFAULT_TTL_SEC)
    except (TypeError, ValueError):
        seconds = DEFAULT_TTL_SEC
    return timedelta(seconds=seconds)


def _num(value: float | None) -> str:
    if value is None:
        return "0"
    return f"{value:.10g}"


def order_line(order: Order) -> str:
    """``id|symbol|side|type|lots|price|sl|tp`` (no ``|`` or newline can appear: symbols are validated)."""
    return "|".join(
        (
            str(order.id),
            order.broker_symbol,
            order.side,
            order.order_type,
            _num(order.lots),
            _num(order.price),
            _num(order.stop_loss),
            _num(order.take_profit),
        )
    )


def parse_fields(raw: bytes | str) -> dict[str, str]:
    """``key=value`` pairs from a form body or plain text (``&`` or one pair per line)."""
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8", errors="replace")
    raw = raw.replace("\r", "").replace("\n", "&").strip().strip("\x00")
    fields: dict[str, str] = {}
    for key, value in parse_qsl(raw, keep_blank_values=True):
        key = key.strip().lower()
        if key and key not in fields:
            fields[key] = value.strip()[:MAX_FIELD]
    return fields


def report_price(fields: dict[str, str]) -> float | None:
    return base.number(fields.get("price"))


def report_volume(fields: dict[str, str]) -> float | None:
    """Lots really filled (``volume=``), when the EA reports a partial fill."""
    value = base.number(fields.get("volume"))
    return value if value is not None and value > 0 else None


def snapshot_from_fields(fields: dict[str, str]) -> dict:
    return base.snapshot_from(
        balance=fields.get("balance"),
        equity=fields.get("equity"),
        floating_pnl=fields.get("profit", fields.get("floating_pnl")),
        margin=fields.get("margin"),
        free_margin=fields.get("free_margin"),
        margin_level=fields.get("margin_level"),
        open_positions=fields.get("positions", fields.get("open_positions")),
    )


def seen_seconds(last_seen_at: datetime | None, now: datetime) -> int | None:
    return None if last_seen_at is None else max(int((now - last_seen_at).total_seconds()), 0)
