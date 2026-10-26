"""External signal ingestion (v1.8): accept signals over HTTP and route them to the capture + auto-execution path.

``POST /api/v1/signals/incoming`` (see ``api/ingest.py``) is protected by a configurable Bearer token whose
sha256 hash is stored in the settings (write-only, never exported). A valid request carries either a raw signal
message (parsed with ``services.signal_parser``) or already-structured fields. The signal is stored like a
captured Telegram signal (``source="api"``) and, when signal auto-execution is armed, sent through the same
guarded path as a Telegram signal (``services.auto_execute.execute_signal``) — so every guardrail, the typed-name
arming, the per-day cap and the checklist gate still apply.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import AccountSettings, Market, TelegramSignal
from . import auto_execute
from .account import local_now
from .i18n import tr
from .signal_parser import ParsedSignal, parse_signal
from .telegram_listener import get_listener, signal_out

TOKEN_MIN_LEN = 16
SOURCE_CHANNEL_MAX = 120
RAW_TEXT_MAX = 4096


class IngestError(ValueError):
    """Invalid ingestion input (the message is returned to the caller)."""


# ------------------------------------------------------------------------- token


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def new_token() -> str:
    """A fresh high-entropy ingestion token (the caller stores it and hands it to the bridge)."""
    return secrets.token_urlsafe(24)


def set_token(settings: AccountSettings, token: str) -> None:
    """Store (or with an empty token remove) the ingestion token. Only its sha256 hash is kept."""
    token = (token or "").strip()
    if not token:
        settings.ingest_token_hash = ""
        settings.ingest_token_hint = ""
        settings.ingest_enabled = False
        return
    if len(token) < TOKEN_MIN_LEN:
        raise IngestError(tr("The token is too short (at least {n} characters)", n=TOKEN_MIN_LEN))
    settings.ingest_token_hash = _hash(token)
    settings.ingest_token_hint = token[-4:]


def token_matches(settings: AccountSettings, presented: str) -> bool:
    """Constant-time check of a presented Bearer token against the stored hash."""
    stored = settings.ingest_token_hash or ""
    if not stored or not presented:
        return False
    return hmac.compare_digest(stored, _hash(presented.strip()))


def status(settings: AccountSettings) -> dict:
    return {
        "enabled": bool(settings.ingest_enabled),
        "token_set": bool(settings.ingest_token_hash),
        "token_hint": settings.ingest_token_hint,
        "endpoint": "/api/v1/signals/incoming",
    }


# ------------------------------------------------------------------ build a signal


@dataclass
class IngestInput:
    text: str = ""
    symbol: str = ""
    side: str = ""  # buy | sell | long | short
    entry: float | None = None
    stop_loss: float | None = None
    take_profits: tuple[float, ...] = ()
    order_type: str = "market"
    source_channel: str = ""


_SIDE_LONG = {"buy", "long", "b", "buy limit", "buy stop"}
_SIDE_SHORT = {"sell", "short", "s", "sell limit", "sell stop"}


def _direction(side: str) -> str | None:
    value = (side or "").strip().lower()
    if value in _SIDE_LONG:
        return "long"
    if value in _SIDE_SHORT:
        return "short"
    return None


def _structured(data: IngestInput) -> tuple[ParsedSignal, str]:
    """A ParsedSignal from already-structured fields, and the text to keep. Raises :class:`IngestError`."""
    direction = _direction(data.side)
    if direction is None:
        raise IngestError(tr("Give the side as buy/sell (or long/short)"))
    symbol = (data.symbol or "").strip().upper()
    if not symbol:
        raise IngestError(tr("Give the symbol"))
    parsed = ParsedSignal(
        symbol=symbol,
        symbol_raw=data.symbol,
        direction=direction,
        order_type=data.order_type if data.order_type in ("market", "limit", "stop") else "market",
        entry=data.entry,
        stop_loss=data.stop_loss,
        take_profits=[tp for tp in data.take_profits if tp is not None],
    )
    tps = " ".join(f"TP{i + 1} {tp:g}" for i, tp in enumerate(parsed.take_profits))
    text = " ".join(
        part
        for part in [
            symbol,
            direction.upper(),
            f"@ {data.entry:g}" if data.entry is not None else "",
            f"SL {data.stop_loss:g}" if data.stop_loss is not None else "",
            tps,
        ]
        if part
    )
    return parsed, text


def build_signal(db: Session, settings: AccountSettings, data: IngestInput, now: datetime) -> TelegramSignal:
    """Parse the input (raw text or structured) and store it as a captured signal (``source="api"``)."""
    source_channel = (data.source_channel or "API").strip()[:SOURCE_CHANNEL_MAX] or "API"
    if data.text and data.text.strip():
        raw = data.text.strip()[:RAW_TEXT_MAX]
        symbols = list(db.scalars(select(Market.symbol).order_by(Market.symbol)))
        parsed = parse_signal(raw, symbols)
    elif data.symbol or data.side:
        parsed, raw = _structured(data)
    else:
        raise IngestError(tr("Send a signal message (text) or at least a symbol and side"))
    ok = bool(parsed.symbol and parsed.direction and parsed.stop_loss is not None)
    signal = TelegramSignal(
        received_at=now,
        chat_id="",
        chat_title=source_channel,
        sender=source_channel,
        source="api",
        raw_text=raw,
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
    db.add(signal)
    db.flush()
    return signal


def ingest(db: Session, settings: AccountSettings, box, data: IngestInput) -> dict:
    """Store the incoming signal and, when signal auto-execution is armed, send it. Returns a summary."""
    now = local_now(settings)
    signal = build_signal(db, settings, data, now)
    db.commit()
    auto = None
    listener = get_listener(db)
    # Auto-execution is gated by the arming (auto_execute + a chosen account), independent of the Telegram-polling
    # switch: a bridge posting here does not need the in-app Telegram bot listener to be running.
    if signal.parse_ok and listener.auto_execute and listener.auto_account_id:
        result = auto_execute.execute_signal(db, settings, box, listener, signal, now)
        auto = {"status": result.status, "message": result.message, "execution_id": result.execution_id}
    return {"signal": signal_out(signal), "auto": auto}
