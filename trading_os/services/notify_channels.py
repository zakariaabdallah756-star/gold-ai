"""Notification channel kinds: config validation, masking and delivery.

Every kind validates its ``config`` (``ChannelConfigError`` carries a message for
the user), knows which fields are secrets (masked in API responses) and turns a
``Message`` into one HTTP request. All network traffic goes through
``_http_post`` so tests can replace it; redirects are never followed.

Delivery functions run on worker threads: they receive plain data (no ORM
objects) and raise ``DeliveryError`` with a short, secret-free message.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from urllib.parse import urlsplit

import httpx

from .. import __version__
from . import webpush

log = logging.getLogger("trading_os.notify")

USER_AGENT = f"TradingOS/{__version__}"
HTTP_TIMEOUT_SEC = 10.0
MASK = "••••"

TELEGRAM_TOKEN_RE = re.compile(r"^\d+:[A-Za-z0-9_-]{30,}$")
TELEGRAM_CHAT_RE = re.compile(r"^(-?\d+|@[A-Za-z][A-Za-z0-9_]{3,31})$")
NTFY_TOPIC_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
NTFY_TOKEN_RE = re.compile(r"^[A-Za-z0-9_.-]{1,200}$")
PUSHOVER_KEY_RE = re.compile(r"^[A-Za-z0-9]{30}$")
DISCORD_HOSTS = ("discord.com", "discordapp.com")

NTFY_PRIORITY = {"low": 2, "default": 3, "high": 5}
PUSHOVER_PRIORITY = {"low": -1, "default": 0, "high": 1}


class ChannelConfigError(ValueError):
    """A channel config the user has to fix (shown as a 422 message)."""


class DeliveryError(Exception):
    """A delivery failed; the message is shown in the history and on the channel."""

    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status  # HTTP status of the failed response, if there was one


class SubscriptionExpired(DeliveryError):
    """The push service no longer knows the browser subscription (HTTP 404/410)."""


@dataclass(frozen=True)
class Message:
    """What a channel sends. ``link`` is absolute (or empty), ``path`` the in-app path."""

    event: str
    title: str
    body: str
    path: str = ""
    link: str = ""
    priority: str = "default"
    tags: list[str] = field(default_factory=list)
    data: dict = field(default_factory=dict)
    sent_at: str = ""  # ISO 8601 with the account's UTC offset


@dataclass(frozen=True)
class VapidKeys:
    private_key: str
    public_key: str
    subject: str


# ----------------------------------------------------------------- helpers


def truncate(text: str, limit: int) -> str:
    text = text.strip()
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def mask_secret(value: str) -> str:
    """``••••`` plus the last 4 characters (just the dots for short values)."""
    if not value:
        return ""
    return MASK + value[-4:] if len(value) > 8 else MASK


def mask_topic(value: str) -> str:
    return value[:2] + MASK if value else ""


def endpoint_id(endpoint: str) -> str:
    """Short, non-reversible id of a push endpoint so a browser can find its own channel."""
    return hashlib.sha256(endpoint.encode()).hexdigest()[:16]


def _text(config: dict, key: str, limit: int = 300) -> str:
    value = config.get(key, "")
    if value is None:
        return ""
    if not isinstance(value, str):
        raise ChannelConfigError(f"{key} must be text")
    value = value.strip()
    if len(value) > limit:
        raise ChannelConfigError(f"{key} is too long (max {limit} characters)")
    return value


def _url(value: str, *, label: str, schemes: tuple[str, ...] = ("https",)) -> str:
    parts = urlsplit(value)
    if parts.scheme not in schemes or not parts.hostname:
        wanted = " or ".join(f"{scheme}://" for scheme in schemes)
        raise ChannelConfigError(f"{label} must be a full {wanted} address")
    if parts.username or parts.password:
        raise ChannelConfigError(f"{label} must not contain a user name or password")
    return value


def _body_from_json(payload: dict) -> bytes:
    return json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def _join(*parts: str) -> str:
    return "\n".join(part for part in parts if part)


# --------------------------------------------------------------- validation


def _validate_webpush(config: dict) -> dict:
    endpoint = _url(_text(config, "endpoint", 1000), label="The push endpoint")
    keys = config.get("keys") or {}
    if not isinstance(keys, dict):
        raise ChannelConfigError("keys must contain p256dh and auth")
    p256dh, auth = _text(keys, "p256dh", 200), _text(keys, "auth", 100)
    if not p256dh or not auth:
        raise ChannelConfigError("The subscription keys p256dh and auth are required")
    try:
        webpush.subscription_keys_valid(p256dh, auth)
    except webpush.WebPushError as exc:
        raise ChannelConfigError(str(exc)) from exc
    return {"endpoint": endpoint, "keys": {"p256dh": p256dh, "auth": auth}}


def _validate_ntfy(config: dict) -> dict:
    server = (_text(config, "server", 200) or "https://ntfy.sh").rstrip("/")
    _url(server, label="The ntfy server", schemes=("https", "http"))
    topic = _text(config, "topic", 64)
    if not topic:
        raise ChannelConfigError("Enter the ntfy topic you subscribed to in the app")
    if not NTFY_TOPIC_RE.match(topic):
        raise ChannelConfigError("The ntfy topic may only use letters, digits, - and _ (max 64)")
    token = _text(config, "token", 200)
    if token and not NTFY_TOKEN_RE.match(token):
        raise ChannelConfigError("The ntfy access token contains invalid characters")
    return {"server": server, "topic": topic, "token": token}


def _validate_telegram(config: dict) -> dict:
    token, chat_id = _text(config, "bot_token", 200), _text(config, "chat_id", 40)
    if not TELEGRAM_TOKEN_RE.match(token):
        raise ChannelConfigError("The bot token looks wrong - copy it from @BotFather (format 123456:ABC…)")
    if not TELEGRAM_CHAT_RE.match(chat_id):
        raise ChannelConfigError("The chat id must be a number (e.g. 123456789 or -100…) or @channelname")
    return {"bot_token": token, "chat_id": chat_id}


def _validate_discord(config: dict) -> dict:
    url = _url(_text(config, "webhook_url", 300), label="The Discord webhook URL")
    parts = urlsplit(url)
    if parts.hostname not in DISCORD_HOSTS or not parts.path.startswith("/api/webhooks/"):
        raise ChannelConfigError("Use a Discord webhook URL (https://discord.com/api/webhooks/…)")
    return {"webhook_url": url}


def _validate_slack(config: dict) -> dict:
    url = _url(_text(config, "webhook_url", 300), label="The Slack webhook URL")
    parts = urlsplit(url)
    if parts.hostname != "hooks.slack.com" or len(parts.path) < 2:
        raise ChannelConfigError("Use a Slack incoming webhook URL (https://hooks.slack.com/…)")
    return {"webhook_url": url}


def _validate_pushover(config: dict) -> dict:
    app_token, user_key = _text(config, "app_token", 40), _text(config, "user_key", 40)
    if not PUSHOVER_KEY_RE.match(app_token):
        raise ChannelConfigError("The Pushover API token must be 30 letters and digits")
    if not PUSHOVER_KEY_RE.match(user_key):
        raise ChannelConfigError("The Pushover user key must be 30 letters and digits")
    return {"app_token": app_token, "user_key": user_key}


def _validate_webhook(config: dict) -> dict:
    url = _url(_text(config, "url", 500), label="The webhook URL", schemes=("https", "http"))
    return {"url": url, "secret": _text(config, "secret", 200)}


# ------------------------------------------------------------------ delivery


def _http_post(url: str, body: bytes, headers: dict[str, str]) -> httpx.Response:
    """The one outbound call of the notification system (patched in tests)."""
    return httpx.post(
        url,
        content=body,
        headers={"User-Agent": USER_AGENT, **headers},
        timeout=HTTP_TIMEOUT_SEC,
        follow_redirects=False,
    )


def _error_detail(response: httpx.Response) -> str:
    """A short reason from a service's error response (known JSON fields only)."""
    try:
        payload = response.json()
    except ValueError:
        return response.reason_phrase or ""
    if not isinstance(payload, dict):
        return response.reason_phrase or ""
    for key in ("description", "message", "error", "errors"):
        value = payload.get(key)
        if isinstance(value, list):
            value = "; ".join(str(item) for item in value)
        if isinstance(value, str) and value:
            return value
    return response.reason_phrase or ""


def post(url: str, body: bytes, headers: dict[str, str], secrets: tuple[str, ...] = ()) -> httpx.Response:
    """POST through ``_http_post`` and turn transport/HTTP failures into ``DeliveryError``."""
    host = urlsplit(url).hostname or "the server"
    try:
        response = _http_post(url, body, headers)
    except httpx.TimeoutException as exc:
        raise DeliveryError(f"Timed out after {HTTP_TIMEOUT_SEC:g} s waiting for {host}") from exc
    except httpx.ConnectError as exc:
        raise DeliveryError(f"Could not connect to {host} - check the address and your internet connection") from exc
    except httpx.HTTPError as exc:
        raise DeliveryError(f"Network error talking to {host} ({type(exc).__name__})") from exc
    if 300 <= response.status_code < 400:
        raise DeliveryError(f"{host} answered with a redirect (HTTP {response.status_code}); use the final address")
    if response.status_code >= 400:
        detail = scrub(truncate(_error_detail(response), 160), secrets)
        raise DeliveryError(f"HTTP {response.status_code}" + (f": {detail}" if detail else ""), response.status_code)
    return response


def scrub(text: str, secrets: tuple[str, ...]) -> str:
    """Remove secret values that a remote service might echo back."""
    for secret in secrets:
        if secret and len(secret) >= 4:
            text = text.replace(secret, MASK)
    return text


JSON_HEADERS = {"Content-Type": "application/json"}


def _send_ntfy(config: dict, message: Message, _vapid: VapidKeys | None) -> None:
    payload = {
        "topic": config["topic"],
        "title": truncate(message.title, 250),
        "message": truncate(message.body, 3500),
        "priority": NTFY_PRIORITY.get(message.priority, 3),
        "tags": [truncate(str(tag), 40) for tag in message.tags[:5]],
    }
    if message.link:
        payload["click"] = message.link
    headers = dict(JSON_HEADERS)
    if config.get("token"):
        headers["Authorization"] = f"Bearer {config['token']}"
    post(config["server"], _body_from_json(payload), headers, (config.get("token", ""), config["topic"]))


def _send_telegram(config: dict, message: Message, _vapid: VapidKeys | None) -> None:
    link = f"\n\n{message.link}" if message.link else ""
    text = truncate(f"{message.title}\n\n{message.body}", 4096 - len(link)) + link
    payload = {"chat_id": config["chat_id"], "text": text, "disable_web_page_preview": True}
    url = f"https://api.telegram.org/bot{config['bot_token']}/sendMessage"
    post(url, _body_from_json(payload), JSON_HEADERS, (config["bot_token"],))


def _send_discord(config: dict, message: Message, _vapid: VapidKeys | None) -> None:
    content = truncate(_join(message.title, message.body, message.link), 2000)
    payload = {"content": content, "allowed_mentions": {"parse": []}}
    post(config["webhook_url"], _body_from_json(payload), JSON_HEADERS, (config["webhook_url"],))


def _slack_escape(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _send_slack(config: dict, message: Message, _vapid: VapidKeys | None) -> None:
    text = _slack_escape(truncate(_join(message.title, message.body, message.link), 3000))
    post(config["webhook_url"], _body_from_json({"text": text}), JSON_HEADERS, (config["webhook_url"],))


def _send_pushover(config: dict, message: Message, _vapid: VapidKeys | None) -> None:
    payload = {
        "token": config["app_token"],
        "user": config["user_key"],
        "title": truncate(message.title, 250),
        "message": truncate(message.body, 1024),
        "priority": PUSHOVER_PRIORITY.get(message.priority, 0),
    }
    if message.link:
        payload["url"] = message.link[:512]
        payload["url_title"] = "Open Trading OS"
    secrets = (config["app_token"], config["user_key"])
    post("https://api.pushover.net/1/messages.json", _body_from_json(payload), JSON_HEADERS, secrets)


def webhook_signature(secret: str, body: bytes) -> str:
    """``sha256=<hex HMAC-SHA256(secret, raw body)>`` (header X-TradingOS-Signature)."""
    return "sha256=" + hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()


def _send_webhook(config: dict, message: Message, _vapid: VapidKeys | None) -> None:
    payload = {
        "event": message.event,
        "title": message.title,
        "body": truncate(message.body, 10_000),
        "url": message.link,
        "priority": message.priority,
        "data": message.data,
        "sent_at": message.sent_at,
    }
    body = _body_from_json(payload)
    headers = dict(JSON_HEADERS)
    if config.get("secret"):
        headers["X-TradingOS-Signature"] = webhook_signature(config["secret"], body)
    post(config["url"], body, headers, (config["url"], config.get("secret", "")))


def webpush_payload(message: Message) -> bytes:
    """JSON read by static/sw.js. The in-app path is used so the click opens the subscribing origin."""
    tag = str(message.data.get("tag") or "")
    item = {
        "title": truncate(message.title, 120),
        "body": truncate(message.body, 1500),
        "url": message.path or "/",
        "tag": tag,
        "priority": message.priority,
        "event": message.event,
    }
    encoded = _body_from_json(item)
    while len(encoded) > webpush.MAX_PAYLOAD_BYTES and len(item["body"]) > 40:
        item["body"] = truncate(item["body"], len(item["body"]) // 2)
        encoded = _body_from_json(item)
    return encoded


def _send_webpush(config: dict, message: Message, vapid: VapidKeys | None) -> None:
    if vapid is None or not vapid.private_key:
        raise DeliveryError("Web Push keys are missing - enable notifications on this device again")
    endpoint, keys = config["endpoint"], config["keys"]
    try:
        body = webpush.encrypt(webpush_payload(message), keys["p256dh"], keys["auth"])
        authorization = webpush.vapid_authorization(endpoint, vapid.private_key, vapid.public_key, vapid.subject)
    except webpush.WebPushError as exc:
        raise DeliveryError(str(exc)) from exc
    headers = {
        "Authorization": authorization,
        "TTL": str(webpush.DEFAULT_TTL_SEC),
        "Urgency": "high" if message.priority == "high" else "normal",
        "Content-Type": "application/octet-stream",
        "Content-Encoding": "aes128gcm",
    }
    try:
        post(endpoint, body, headers, (endpoint, keys["auth"]))
    except DeliveryError as exc:
        if exc.status in (404, 410):
            raise SubscriptionExpired("Subscription expired") from exc
        raise


# -------------------------------------------------------------------- kinds

Sender = Callable[[dict, Message, "VapidKeys | None"], None]


@dataclass(frozen=True)
class Kind:
    label: str
    validate: Callable[[dict], dict]
    send: Sender
    secret_fields: tuple[str, ...] = ()


KINDS: dict[str, Kind] = {
    "webpush": Kind("Web Push", _validate_webpush, _send_webpush),
    "ntfy": Kind("ntfy", _validate_ntfy, _send_ntfy, ("token",)),
    "telegram": Kind("Telegram", _validate_telegram, _send_telegram, ("bot_token",)),
    "discord": Kind("Discord", _validate_discord, _send_discord, ("webhook_url",)),
    "slack": Kind("Slack", _validate_slack, _send_slack, ("webhook_url",)),
    "pushover": Kind("Pushover", _validate_pushover, _send_pushover, ("app_token", "user_key")),
    "webhook": Kind("Webhook", _validate_webhook, _send_webhook, ("url", "secret")),
}


def validate(kind: str, config: dict) -> dict:
    if kind not in KINDS:
        raise ChannelConfigError(f"Unknown channel type: {kind}")
    if not isinstance(config, dict):
        raise ChannelConfigError("config must be an object")
    return KINDS[kind].validate(config)


def masked_config(kind: str, config: dict) -> dict:
    """The config as the API shows it: secrets masked, Web Push reduced to the push service host."""
    if kind == "webpush":
        return {"push_service": urlsplit(config.get("endpoint", "")).hostname or ""}
    masked = dict(config)
    for key in KINDS[kind].secret_fields:
        masked[key] = mask_secret(str(config.get(key) or ""))
    if kind == "ntfy":
        masked["topic"] = mask_topic(str(config.get("topic") or ""))
    return masked


def merge_config(kind: str, stored: dict, incoming: dict) -> dict:
    """Keep stored secrets that come back unchanged in their masked form, then validate."""
    if kind == "webpush":
        return stored
    merged = dict(incoming)
    masked = masked_config(kind, stored)
    for key in (*KINDS[kind].secret_fields, "topic"):
        if key in stored and merged.get(key) == masked.get(key) and masked.get(key):
            merged[key] = stored[key]
    return validate(kind, merged)


def summary(kind: str, config: dict) -> str:
    """One line describing where a channel delivers, without secrets."""
    masked = masked_config(kind, config)
    if kind == "webpush":
        return f"Browser push via {masked['push_service'] or 'push service'}"
    if kind == "ntfy":
        return f"Topic {masked['topic']} on {urlsplit(config.get('server', '')).hostname}"
    if kind == "telegram":
        return f"Chat {config.get('chat_id', '')}"
    if kind == "pushover":
        return f"User key {masked['user_key']}"
    host = urlsplit(config.get("webhook_url") or config.get("url") or "").hostname or ""
    return f"Webhook to {host}" + (" (signed)" if config.get("secret") else "")


def deliver(kind: str, config: dict, message: Message, vapid: VapidKeys | None = None) -> None:
    """Send ``message`` through one channel; raises DeliveryError on failure."""
    KINDS[kind].send(config, message, vapid)
