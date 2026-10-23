"""What every order bridge shares: the order and result types, config checks, symbol mapping, HTTP helpers.

A bridge never sees ORM objects: :func:`services.execution.bridge_context` hands it a :class:`BridgeContext`
(plain account fields, the non-secret ``connection_config`` and the decrypted secrets it needs) and an
:class:`Order`. It answers with a :class:`BridgeResult` whose ``response`` must be secret-free (the execution
service scrubs it again before storing it). All outbound HTTP goes through :func:`_http_post` /
:func:`_http_request` so tests can replace them; redirects are never followed.
"""

from __future__ import annotations

import hashlib
import hmac
import ipaddress
import json
import re
from dataclasses import dataclass, field
from datetime import datetime
from urllib.parse import urlsplit

import httpx

from ... import __version__
from ..i18n import tr

USER_AGENT = f"TradingOS/{__version__}"
HTTP_TIMEOUT = httpx.Timeout(15.0, connect=5.0)
RESULT_STATUSES = ("sent", "filled", "pending", "rejected", "error")
ORDER_TYPES = ("market", "limit", "stop")
MASK = "••••"
SUFFIX_RE = re.compile(r"^[A-Za-z0-9._#+-]{0,12}$")
BROKER_SYMBOL_RE = re.compile(r"^[A-Za-z0-9._#+/-]{1,40}$")
JOURNAL_SYMBOL_RE = re.compile(r"^[A-Z0-9._#+/-]{1,20}$")
MAX_SYMBOL_MAP = 100
# Keys dropped from anything stored in an Execution (request / response), whatever the bridge sent back.
SECRET_KEY_PARTS = ("password", "secret", "token", "signature", "authorization", "api_key", "apikey")
_SHARED_NETWORK = ipaddress.ip_network("100.64.0.0/10")  # carrier-grade NAT / Tailscale


class BridgeConfigError(ValueError):
    """A connection setting the trader has to fix (shown as a 422 message)."""


class BridgeUnavailable(Exception):
    """The bridge cannot run on this machine (e.g. the MetaTrader5 package is missing)."""


@dataclass(frozen=True)
class Order:
    """One order as a bridge sends it. ``id`` is the Execution id (unique, increasing)."""

    id: int
    idempotency_key: str
    symbol: str  # journal symbol
    broker_symbol: str  # after the account's symbol suffix / map
    direction: str  # long | short
    order_type: str  # market | limit | stop
    lots: float
    price: float | None  # reference price (market) or the order price (limit / stop)
    stop_loss: float | None = None
    take_profit: float | None = None
    contract_size: float | None = None  # units per lot, from the journal market
    price_decimals: int | None = None
    comment: str = ""

    @property
    def side(self) -> str:
        return "buy" if self.direction == "long" else "sell"

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "idempotency_key": self.idempotency_key,
            "symbol": self.symbol,
            "broker_symbol": self.broker_symbol,
            "side": self.side,
            "direction": self.direction,
            "type": self.order_type,
            "lots": self.lots,
            "price": self.price,
            "sl": self.stop_loss,
            "tp": self.take_profit,
            "comment": self.comment,
        }


@dataclass
class BridgeResult:
    status: str  # sent | filled | pending | rejected | error (cancel: cancelled)
    broker_ref: str = ""
    fill_price: float | None = None
    message: str = ""
    response: dict = field(default_factory=dict)  # secret-free details kept on the Execution

    @property
    def accepted(self) -> bool:
        return self.status in ("sent", "filled", "pending")


@dataclass
class TestResult:
    __test__ = False  # not a pytest class

    ok: bool
    message: str
    snapshot: dict | None = None


@dataclass(frozen=True)
class BridgeContext:
    """What a bridge may know about the account. ``secrets`` are in clear: never log or return them."""

    account_id: int
    account_name: str
    login: str = ""
    server: str = ""
    currency: str = "USD"
    config: dict = field(default_factory=dict)
    secrets: dict = field(default_factory=dict, repr=False)
    last_seen_at: datetime | None = None
    now: datetime | None = None

    def secret(self, kind: str) -> str:
        return self.secrets.get(kind, "") or ""

    def secret_values(self) -> tuple[str, ...]:
        return tuple(value for value in self.secrets.values() if value)


class Bridge:
    """Base class. Subclasses set the class attributes and implement :meth:`submit` and :meth:`test`."""

    kind = ""
    secrets: tuple[str, ...] = ()  # credential kinds the bridge uses
    destination_keys: tuple[str, ...] = ()  # config keys whose change switches order sending off
    polling = False  # orders wait in Trading OS until the trader's terminal fetches them
    cancellable = False  # a pending order can be cancelled from Trading OS

    # ------------------------------------------------------------- configuration

    def validate_config(self, config: dict) -> dict:
        """The cleaned, non-secret ``connection_config`` (raises :class:`BridgeConfigError`)."""
        if not isinstance(config, dict):
            raise BridgeConfigError(tr("The connection settings must be an object"))
        cleaned = common_config(config)
        cleaned.update(self._validate(config))
        return cleaned

    def _validate(self, config: dict) -> dict:
        return {}

    def missing(self, ctx: BridgeContext) -> str | None:
        """What must still be set before orders can be sent (None = ready)."""
        return None

    def broker_symbol(self, symbol: str, config: dict, base_currency: str | None = None, quote: str = "") -> str:
        mapped = (config.get("symbol_map") or {}).get(symbol)
        if mapped:
            return mapped
        return symbol + (config.get("symbol_suffix") or "")

    # ------------------------------------------------------------- orders

    def submit(self, ctx: BridgeContext, order: Order) -> BridgeResult:  # pragma: no cover - abstract
        raise NotImplementedError

    def cancel(self, ctx: BridgeContext, order: Order, broker_ref: str) -> BridgeResult:
        return BridgeResult("cancelled", broker_ref, message=tr("Cancelled in Trading OS"))

    def snapshot(self, ctx: BridgeContext) -> dict | None:
        """Balance, equity, floating P&L, margin, free margin, margin level and open positions (None = unknown)."""
        return None

    def test(self, ctx: BridgeContext) -> TestResult:  # pragma: no cover - abstract
        raise NotImplementedError


# --------------------------------------------------------------------------- config helpers


def text(config: dict, key: str, limit: int = 200) -> str:
    value = config.get(key, "")
    if value is None:
        return ""
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        value = str(value)
    if not isinstance(value, str):
        raise BridgeConfigError(tr("{key} must be text", key=key))
    value = value.strip()
    if len(value) > limit:
        raise BridgeConfigError(tr("{key} is too long (max {limit} characters)", key=key, limit=limit))
    return value


def integer(config: dict, key: str, default: int, low: int, high: int) -> int:
    value = config.get(key, default)
    if value in (None, ""):
        return default
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise BridgeConfigError(tr("{key} must be a whole number", key=key)) from exc
    if isinstance(value, float) and value != number:
        raise BridgeConfigError(tr("{key} must be a whole number", key=key))
    if not low <= number <= high:
        raise BridgeConfigError(tr("{key} must be between {low} and {high}", key=key, low=low, high=high))
    return number


def common_config(config: dict) -> dict:
    """``symbol_suffix`` and ``symbol_map`` (journal symbol -> broker symbol), shared by every bridge."""
    suffix = text(config, "symbol_suffix", 12)
    if not SUFFIX_RE.match(suffix):
        raise BridgeConfigError(tr("The symbol suffix may only contain letters, digits and . _ # + -"))
    raw_map = config.get("symbol_map") or {}
    if not isinstance(raw_map, dict):
        raise BridgeConfigError(tr("The symbol map must pair journal symbols with broker symbols"))
    if len(raw_map) > MAX_SYMBOL_MAP:
        raise BridgeConfigError(tr("The symbol map can hold at most {count} symbols", count=MAX_SYMBOL_MAP))
    symbol_map = {}
    for journal, broker in raw_map.items():
        journal = str(journal).strip().upper()
        broker = str(broker if broker is not None else "").strip()
        if not journal and not broker:
            continue
        if not JOURNAL_SYMBOL_RE.match(journal) or not BROKER_SYMBOL_RE.match(broker):
            raise BridgeConfigError(
                tr("Invalid symbol map entry: {journal} = {broker}", journal=journal, broker=broker)
            )
        symbol_map[journal] = broker
    return {"symbol_suffix": suffix, "symbol_map": symbol_map}


def is_local_host(host: str) -> bool:
    """localhost or a loopback / private (LAN, VPN) IP address: plain http is acceptable there."""
    name = host.strip("[]").lower().rstrip(".")
    if name == "localhost" or name.endswith(".localhost"):
        return True
    try:
        ip = ipaddress.ip_address(name)
    except ValueError:
        return False
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    return ip.is_loopback or ip.is_private or ip.is_link_local or ip in _SHARED_NETWORK


def checked_url(value: str, *, label: str) -> str:
    """An https URL, or http to localhost / a LAN address. No user name or password in it."""
    try:
        parts = urlsplit(value)
        parts.port  # noqa: B018 - raises ValueError for a bad port
    except ValueError as exc:
        raise BridgeConfigError(tr("{label} is not a valid address", label=label)) from exc
    if parts.scheme not in ("https", "http") or not parts.hostname:
        raise BridgeConfigError(tr("{label} must be a full https:// address", label=label))
    if parts.username or parts.password:
        raise BridgeConfigError(tr("{label} must not contain a user name or password", label=label))
    if parts.scheme == "http" and not is_local_host(parts.hostname):
        raise BridgeConfigError(
            tr("{label} must use https:// (plain http only for localhost or a LAN address)", label=label)
        )
    return value


# --------------------------------------------------------------------------- results and secrets


def number(value) -> float | None:
    """A positive finite float from JSON / text, else None."""
    if isinstance(value, bool) or value is None:
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if result == result and result not in (float("inf"), float("-inf")) and result > 0 else None


def snapshot_from(
    *,
    balance=None,
    equity=None,
    floating_pnl=None,
    margin=None,
    free_margin=None,
    margin_level=None,
    open_positions=None,
) -> dict:
    """A ``TradingAccount.snapshot`` from raw numbers (missing ones are left out)."""

    def money(value):
        if value is None or isinstance(value, bool):
            return None
        try:
            result = float(value)
        except (TypeError, ValueError):
            return None
        return round(result, 2) if result == result and abs(result) != float("inf") else None

    out = {
        "balance": money(balance),
        "equity": money(equity),
        "floating_pnl": money(floating_pnl),
        "margin": money(margin),
        "free_margin": money(free_margin),
        "margin_level": money(margin_level),
    }
    if not out["margin"]:
        out["margin_level"] = None  # no margin in use: the level is undefined (MetaTrader reports 0)
    elif out["margin_level"] is None and out["equity"] is not None:
        out["margin_level"] = round(out["equity"] / out["margin"] * 100, 2)
    if out["free_margin"] is None and out["equity"] is not None and out["margin"] is not None:
        out["free_margin"] = round(out["equity"] - out["margin"], 2)
    try:
        positions = int(float(open_positions)) if open_positions not in (None, "") else None
    except (TypeError, ValueError):
        positions = None
    if positions is not None:
        out["open_positions"] = max(positions, 0)
    return {key: value for key, value in out.items() if value is not None}


def sanitize(value, secrets: tuple[str, ...] = (), depth: int = 0):
    """``value`` without secret-looking keys or known secret values, safe to store and show."""
    if depth > 8:
        return None
    if isinstance(value, dict):
        return {
            str(key): sanitize(item, secrets, depth + 1)
            for key, item in value.items()
            if not any(part in str(key).lower().replace("-", "_") for part in SECRET_KEY_PARTS)
        }
    if isinstance(value, (list, tuple)):
        return [sanitize(item, secrets, depth + 1) for item in list(value)[:50]]
    if isinstance(value, str):
        for secret in secrets:
            if secret and len(secret) >= 4:
                value = value.replace(secret, MASK)
        return value[:2000]
    if isinstance(value, float) and value != value:
        return None
    return value


def truncate(message: str, limit: int = 300) -> str:
    message = " ".join(str(message).split())
    return message if len(message) <= limit else message[: limit - 1].rstrip() + "…"


# --------------------------------------------------------------------------- HTTP


def json_body(payload: dict) -> bytes:
    return json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def signature(secret: str, body: bytes) -> str:
    """``sha256=<hex HMAC-SHA256(secret, raw body)>``: the X-TradingOS-Signature of notification webhooks too."""
    return "sha256=" + hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()


def _http_post(url: str, body: bytes, headers: dict[str, str]) -> httpx.Response:
    """The webhook bridge's one outbound call (patched in tests)."""
    return httpx.post(
        url,
        content=body,
        headers={"User-Agent": USER_AGENT, **headers},
        timeout=HTTP_TIMEOUT,
        follow_redirects=False,
    )


def _http_request(method: str, url: str, headers: dict[str, str], body: bytes | None = None) -> httpx.Response:
    """Broker REST calls (patched in tests)."""
    return httpx.request(
        method,
        url,
        content=body,
        headers={"User-Agent": USER_AGENT, **headers},
        timeout=HTTP_TIMEOUT,
        follow_redirects=False,
    )


def transport_error(exc: Exception, host: str, *, order: bool) -> str:
    """A short, secret-free message for a failed HTTP call. For orders, a timeout leaves the outcome unknown."""
    if isinstance(exc, httpx.TimeoutException):
        if order:
            return tr(
                "No answer from {host} in time - the order may or may not have arrived: check your platform "
                "before sending it again",
                host=host,
            )
        return tr("No answer from {host} in time", host=host)
    if isinstance(exc, httpx.ConnectError):
        return tr("Could not connect to {host} - check the address and your internet connection", host=host)
    return tr("Network error talking to {host} ({error})", host=host, error=type(exc).__name__)


def response_json(response: httpx.Response):
    try:
        return response.json()
    except ValueError:
        return None


def error_detail(payload, response: httpx.Response) -> str:
    """A short reason from an error response (known JSON fields only)."""
    if isinstance(payload, dict):
        for key in ("errorMessage", "message", "error", "reason", "detail", "description"):
            value = payload.get(key)
            if isinstance(value, str) and value:
                return value
    return response.reason_phrase or ""
