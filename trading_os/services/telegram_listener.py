"""Telegram signal listener (v1.7), Bot API only.

The listener reads messages a Telegram **bot** receives — posts in channels and groups the bot has been added
to, and messages a person forwards or sends straight to the bot — through ``getUpdates``. It never signs in as a
person and never works around a chat's content protection: a message from a chat that has content protection
turned on is skipped when ``skip_protected`` is set, and its text is not stored. Each message is parsed with
``services.signal_parser`` and kept as a ``TelegramSignal`` for the Signals page (one tap to load the ticket),
or, when the listener's optional guarded auto-execution is armed, sent through ``services.auto_execute``.

Set-up: create a bot with @BotFather, paste its token here, add the bot to the channel/group (or forward
signals to it). Bots cannot read other people's accounts, only what they are sent.

I/O is one function, ``_http_get`` (patched in tests). Everything else is pure and takes the fetched updates.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import httpx
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..models import AccountSettings, Market, TelegramListener, TelegramSignal
from . import secrets_store
from .account import local_now
from .i18n import N_, tr
from .signal_parser import parse_signal

log = logging.getLogger("trading_os.telegram")

API_BASE = "https://api.telegram.org"
HTTP_TIMEOUT_SEC = 12.0
POLL_TIMEOUT_SEC = 0  # getUpdates long-poll off: the scheduler drives the cadence
MAX_UPDATES = 50
MAX_TEXT = 4096
MAX_CAPTURED = 500  # oldest captured signals beyond this are trimmed
JOB_NAME = "telegram-listener"
TOKEN_RE_LEN = (20, 60)


class TelegramError(RuntimeError):
    """A problem talking to Telegram, or a misconfiguration (message shown to the trader)."""


@dataclass
class Candidate:
    """One message worth considering, extracted from an update (pure)."""

    update_id: int
    chat_id: str
    chat_title: str
    message_id: int | None
    sender: str
    source: str
    text: str
    protected: bool = False


@dataclass
class PollOutcome:
    polled: int = 0
    captured: int = 0
    skipped_protected: int = 0
    skipped_other: int = 0
    highest_update_id: int = 0
    candidates: list[Candidate] = field(default_factory=list)


# --------------------------------------------------------------------------- config


def get_listener(db: Session) -> TelegramListener:
    row = db.get(TelegramListener, 1)
    if row is None:
        row = TelegramListener(id=1)
        db.add(row)
        db.commit()
    return row


def set_token(db: Session, box, listener: TelegramListener, token: str) -> None:
    token = (token or "").strip()
    if not token:
        listener.token_enc = ""
        listener.token_hint = ""
        listener.bot_username = ""
        return
    if not _looks_like_token(token):
        raise TelegramError(tr("That does not look like a bot token (it looks like 123456:ABC… from @BotFather)"))
    listener.token_enc = box.encrypt(token)
    listener.token_hint = secrets_store.hint(token)


def _looks_like_token(token: str) -> bool:
    if ":" not in token or not (TOKEN_RE_LEN[0] <= len(token) <= TOKEN_RE_LEN[1]):
        return False
    head, _, tail = token.partition(":")
    return head.isdigit() and tail.replace("-", "").replace("_", "").isalnum()


def read_token(box, listener: TelegramListener) -> str:
    if not listener.token_enc:
        return ""
    try:
        return box.decrypt(listener.token_enc)
    except secrets_store.SecretError as exc:
        raise TelegramError(tr("The bot token cannot be read on this computer - paste it again")) from exc


# ----------------------------------------------------------------- parsing updates


def _chat_fields(chat: dict) -> tuple[str, str, str]:
    """(chat_id, title, source) from a Telegram chat object."""
    chat_id = str(chat.get("id", ""))
    kind = chat.get("type", "")
    title = chat.get("title") or chat.get("username") or chat.get("first_name") or chat_id
    source = {"channel": "channel", "supergroup": "group", "group": "group", "private": "direct"}.get(kind, "channel")
    return chat_id, str(title)[:160], source


def _sender(message: dict) -> str:
    fwd = message.get("forward_from_chat") or message.get("forward_from")
    if isinstance(fwd, dict):
        name = fwd.get("title") or fwd.get("username") or fwd.get("first_name")
        if name:
            return str(name)[:120]
    author = message.get("author_signature")
    if author:
        return str(author)[:120]
    user = message.get("from")
    if isinstance(user, dict):
        return str(user.get("username") or user.get("first_name") or "")[:120]
    return ""


def extract_candidate(update: dict, skip_protected: bool) -> Candidate | None:
    """A :class:`Candidate` from one update, or None when there is no text to read."""
    update_id = int(update.get("update_id", 0))
    message = (
        update.get("channel_post")
        or update.get("message")
        or update.get("edited_channel_post")
        or update.get("edited_message")
    )
    if not isinstance(message, dict):
        return None
    chat = message.get("chat")
    if not isinstance(chat, dict):
        return None
    chat_id, title, source = _chat_fields(chat)
    is_forward = bool(message.get("forward_from_chat") or message.get("forward_from") or message.get("forward_origin"))
    if source == "direct" and is_forward:
        source = "forward"
    protected = bool(message.get("has_protected_content") or chat.get("has_protected_content"))
    text = (message.get("text") or message.get("caption") or "")[:MAX_TEXT]
    if protected and skip_protected:
        # Respect the chat owner's content protection: record that we skipped it, keep no text.
        return Candidate(update_id, chat_id, title, message.get("message_id"), _sender(message), source, "", True)
    if not text.strip():
        return None
    return Candidate(update_id, chat_id, title, message.get("message_id"), _sender(message), source, text)


def chat_allowed(candidate: Candidate, allowed: list) -> bool:
    """Whether the listener accepts this chat ([] = any chat the bot receives)."""
    if not allowed:
        return True
    wanted = {str(a).lstrip("@").lower() for a in allowed}
    return candidate.chat_id.lower() in wanted or candidate.chat_title.lstrip("@").lower() in wanted


def process_updates(updates: list[dict], listener: TelegramListener) -> PollOutcome:
    """Turn raw updates into a :class:`PollOutcome` (pure). Offset and allow-list are applied here."""
    outcome = PollOutcome(polled=len(updates))
    for update in updates:
        uid = int(update.get("update_id", 0))
        outcome.highest_update_id = max(outcome.highest_update_id, uid)
        candidate = extract_candidate(update, listener.skip_protected)
        if candidate is None:
            outcome.skipped_other += 1
            continue
        if candidate.protected:
            outcome.skipped_protected += 1
            continue
        if not chat_allowed(candidate, listener.allowed_chats or []):
            outcome.skipped_other += 1
            continue
        outcome.candidates.append(candidate)
    return outcome


# --------------------------------------------------------------------- persistence


def _signal_from(db: Session, candidate: Candidate, symbols: list[str], now: datetime) -> TelegramSignal:
    parsed = parse_signal(candidate.text, symbols)
    ok = bool(parsed.symbol and parsed.direction and parsed.stop_loss is not None)
    return TelegramSignal(
        received_at=now,
        update_id=candidate.update_id,
        chat_id=candidate.chat_id,
        chat_title=candidate.chat_title,
        message_id=candidate.message_id,
        sender=candidate.sender,
        source=candidate.source,
        raw_text=candidate.text,
        symbol=parsed.symbol or "",
        direction=parsed.direction or "",
        order_type=parsed.order_type,
        entry_price=parsed.entry,
        stop_loss=parsed.stop_loss,
        take_profits=list(parsed.take_profits or []),
        parse_ok=ok,
        warnings=list(parsed.warnings or []),
        status="new",
    )


def store_candidates(
    db: Session, settings: AccountSettings, outcome: PollOutcome, now: datetime
) -> list[TelegramSignal]:
    symbols = list(db.scalars(select(Market.symbol).order_by(Market.symbol)))
    stored: list[TelegramSignal] = []
    for candidate in outcome.candidates:
        signal = _signal_from(db, candidate, symbols, now)
        db.add(signal)
        stored.append(signal)
    db.flush()
    _trim(db)
    return stored


def _trim(db: Session) -> None:
    total = db.scalar(select(func.count(TelegramSignal.id))) or 0
    if total <= MAX_CAPTURED:
        return
    old = db.scalars(select(TelegramSignal.id).order_by(TelegramSignal.id).limit(total - MAX_CAPTURED)).all()
    for sid in old:
        db.delete(db.get(TelegramSignal, sid))


# ------------------------------------------------------------------------ polling


def _http_get(url: str, params: dict) -> httpx.Response:
    """The one outbound call of the listener (patched in tests)."""
    return httpx.get(url, params=params, timeout=HTTP_TIMEOUT_SEC, follow_redirects=False)


def _call(token: str, method: str, params: dict | None = None) -> Any:
    url = f"{API_BASE}/bot{token}/{method}"
    try:
        response = _http_get(url, params or {})
    except httpx.TimeoutException as exc:
        raise TelegramError(tr("Timed out waiting for Telegram")) from exc
    except httpx.HTTPError as exc:
        raise TelegramError(tr("Could not reach Telegram ({error})", error=type(exc).__name__)) from exc
    try:
        payload = response.json()
    except ValueError as exc:
        raise TelegramError(tr("Telegram sent an unexpected response")) from exc
    if not isinstance(payload, dict) or not payload.get("ok"):
        detail = payload.get("description") if isinstance(payload, dict) else ""
        if response.status_code == 401 or "unauthorized" in str(detail).lower():
            raise TelegramError(tr("Telegram rejected the bot token - check it with @BotFather"))
        raise TelegramError(tr("Telegram error: {detail}", detail=str(detail)[:150] or response.status_code))
    return payload.get("result")


def check_token(token: str) -> dict:
    """Validate a token against ``getMe``; returns {username, name}. Raises :class:`TelegramError`."""
    me = _call(token, "getMe")
    if not isinstance(me, dict):
        raise TelegramError(tr("Telegram sent an unexpected response"))
    return {"username": me.get("username", ""), "name": me.get("first_name", "")}


def fetch_updates(token: str, offset: int) -> list[dict]:
    result = _call(token, "getUpdates", {"offset": offset, "limit": MAX_UPDATES, "timeout": POLL_TIMEOUT_SEC})
    return result if isinstance(result, list) else []


def poll(db: Session, settings: AccountSettings, box, *, auto_hook=None) -> dict:
    """Fetch new updates, store captured signals, advance the offset. ``auto_hook(db, settings, box, signals)``
    (optional) runs the guarded auto-execution. Returns a summary. Raises :class:`TelegramError` on failure."""
    listener = get_listener(db)
    token = read_token(box, listener)
    now = local_now(settings)
    listener.last_poll_at = now
    if not token:
        listener.last_error = tr("No bot token set.")
        db.commit()
        raise TelegramError(listener.last_error)
    try:
        updates = fetch_updates(token, listener.last_update_id + 1 if listener.last_update_id else 0)
    except TelegramError as exc:
        listener.last_error = str(exc)[:300]
        db.commit()
        raise
    outcome = process_updates(updates, listener)
    stored = store_candidates(db, settings, outcome, now)
    if outcome.highest_update_id:
        listener.last_update_id = max(listener.last_update_id, outcome.highest_update_id)
    listener.captured = (listener.captured or 0) + len(stored)
    listener.last_ok_at = now
    listener.last_error = ""
    db.commit()
    auto = auto_hook(db, settings, box, stored) if auto_hook and stored else []
    return {
        "polled": outcome.polled,
        "captured": len(stored),
        "skipped_protected": outcome.skipped_protected,
        "skipped_other": outcome.skipped_other,
        "auto": auto,
    }


# -------------------------------------------------------------------------- jobs


def register_jobs(scheduler, config) -> None:
    """Background poller; runs only while the listener is enabled and a token is set."""

    def run(session_factory):
        from . import auto_execute  # local import: keeps the execution dependency off this module's import

        with session_factory() as db:
            from .account import get_settings

            settings = get_settings(db)
            listener = get_listener(db)
            if not (listener.enabled and listener.token_enc):
                return None
            box = getattr(scheduler, "secrets", None)
            try:
                return poll(db, settings, box, auto_hook=auto_execute.run_for_telegram)
            except TelegramError as exc:
                return {"error": str(exc)}

    scheduler.register(JOB_NAME, _interval, run, description="Telegram signal listener", initial_delay=15.0)


def _interval() -> float:
    return 20.0


# -------------------------------------------------------------------------- output


STATUS_LABELS = {"new": N_("New"), "sent": N_("Sent"), "dismissed": N_("Dismissed")}


def signal_out(signal: TelegramSignal) -> dict:
    return {
        "id": signal.id,
        "received_at": signal.received_at.isoformat() if signal.received_at else None,
        "chat_title": signal.chat_title,
        "sender": signal.sender,
        "source": signal.source,
        "raw_text": signal.raw_text,
        "symbol": signal.symbol,
        "direction": signal.direction,
        "order_type": signal.order_type,
        "entry_price": signal.entry_price,
        "stop_loss": signal.stop_loss,
        "take_profits": list(signal.take_profits or []),
        "parse_ok": signal.parse_ok,
        "warnings": list(signal.warnings or []),
        "status": signal.status,
        "auto_result": signal.auto_result,
        "execution_id": signal.execution_id,
    }


def status_out(db: Session, box, listener: TelegramListener) -> dict:
    return {
        "enabled": listener.enabled,
        "token_set": bool(listener.token_enc),
        "token_hint": listener.token_hint,
        "bot_username": listener.bot_username,
        "allowed_chats": list(listener.allowed_chats or []),
        "skip_protected": listener.skip_protected,
        "auto_account_id": listener.auto_account_id,
        "auto_execute": listener.auto_execute,
        "risk_pct": listener.risk_pct,
        "max_trades_per_day": listener.max_trades_per_day,
        "require_checklist": listener.require_checklist,
        "last_poll_at": listener.last_poll_at.isoformat() if listener.last_poll_at else None,
        "last_ok_at": listener.last_ok_at.isoformat() if listener.last_ok_at else None,
        "last_error": listener.last_error,
        "captured": listener.captured,
        "new_count": db.scalar(select(func.count(TelegramSignal.id)).where(TelegramSignal.status == "new")) or 0,
    }
