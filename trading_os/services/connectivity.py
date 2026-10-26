"""Connectivity status (v1.7): internet reachability, broker bridge freshness and the Telegram listener state.

The internet probe is best-effort and cached, so the status endpoint never blocks the UI or hammers the
network. ``probe`` is injectable for tests. Everything else is read from the database (account snapshot/last
contact times, the Telegram listener row), so it needs no network at all.
"""

from __future__ import annotations

import logging
import socket
import time
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import AccountSettings, TelegramListener, TradingAccount
from .account import local_now
from .i18n import N_, tr

log = logging.getLogger("trading_os.connectivity")

PROBE_HOSTS = (("1.1.1.1", 443), ("8.8.8.8", 443))  # well-known anycast resolvers; DNS-free, no payload sent
PROBE_TIMEOUT_SEC = 2.0
PROBE_CACHE_SEC = 30.0
# A bridge counts as live while its last contact is this recent; a polling EA refreshes it on every poll.
BRIDGE_FRESH_SEC = 300
BRIDGE_IDLE_SEC = 900


@dataclass
class InternetStatus:
    online: bool
    checked_at: float  # monotonic time of the probe
    at: datetime | None = None  # wall-clock of the probe


def _tcp_probe() -> bool:
    """True if any probe host accepts a TCP connection quickly (no data is sent). Best-effort."""
    for host, port in PROBE_HOSTS:
        try:
            with socket.create_connection((host, port), timeout=PROBE_TIMEOUT_SEC):
                return True
        except OSError:
            continue
    return False


class InternetMonitor:
    """Caches the last probe so the status endpoint is cheap and never blocks for long."""

    def __init__(self, probe=_tcp_probe):
        self._probe = probe
        self._cached: InternetStatus | None = None

    def status(self, now: datetime, *, force: bool = False) -> InternetStatus:
        monotonic = time.monotonic()
        if not force and self._cached is not None and monotonic - self._cached.checked_at < PROBE_CACHE_SEC:
            return self._cached
        try:
            online = bool(self._probe())
        except Exception:  # noqa: BLE001 - a probe failure just means "treat as offline"
            online = False
        self._cached = InternetStatus(online=online, checked_at=monotonic, at=now)
        return self._cached


# --------------------------------------------------------------------------- bridges

BRIDGE_STATES = {
    "live": N_("Live"),
    "stale": N_("No recent data"),
    "idle": N_("Idle"),
    "error": N_("Error"),
    "off": N_("Not sending"),
}


def _age_seconds(moment: datetime | None, now: datetime) -> float | None:
    if moment is None:
        return None
    return max(0.0, (now - moment).total_seconds())


def _bridge_state(account: TradingAccount, age: float | None) -> str:
    if account.last_error:
        return "error"
    if account.connection == "paper":
        return "live"  # simulated: always ready
    if age is None:
        return "idle"
    if age <= BRIDGE_FRESH_SEC:
        return "live"
    if age <= BRIDGE_IDLE_SEC:
        return "idle"
    return "stale"


def _bridge_status(settings: AccountSettings, account: TradingAccount, now: datetime) -> dict:
    last = account.last_seen_at or account.snapshot_at
    age = _age_seconds(last, now)
    state = _bridge_state(account, age)
    return {
        "id": account.id,
        "name": settings.account_name if account.id == settings.default_account_id else account.name,
        "connection": account.connection,
        "sending": bool(account.execution_enabled),
        "state": state,
        "state_label": tr(BRIDGE_STATES[state]),
        "last_seen_at": last.isoformat() if last else None,
        "age_sec": round(age) if age is not None else None,
        "last_error": account.last_error,
    }


def bridges(db: Session, settings: AccountSettings, now: datetime) -> list[dict]:
    """One row per active account that has a connection (order routing), newest-contact first."""
    rows = db.scalars(
        select(TradingAccount).where(TradingAccount.status == "active", TradingAccount.connection != "none")
    )
    out = [_bridge_status(settings, account, now) for account in rows]
    out.sort(key=lambda r: (r["state"] != "error", r["state"] != "stale", r["name"].lower()))
    return out


# -------------------------------------------------------------------------- telegram


def telegram_status(db: Session) -> dict:
    row = db.get(TelegramListener, 1)
    if row is None or not row.token_enc:
        return {"configured": False, "enabled": False, "ok": False, "state": "off"}
    if not row.enabled:
        state = "off"
    elif row.last_error:
        state = "error"
    elif row.last_ok_at:
        state = "live"
    else:
        state = "idle"
    return {
        "configured": True,
        "enabled": row.enabled,
        "ok": state == "live",
        "state": state,
        "bot_username": row.bot_username,
        "last_ok_at": row.last_ok_at.isoformat() if row.last_ok_at else None,
        "last_error": row.last_error,
    }


# --------------------------------------------------------------------------- overall


def overall_level(internet_online: bool, bridge_rows: list[dict], telegram: dict) -> str:
    """ok | warn | down for the single top-bar indicator."""
    if not internet_online:
        return "down"
    sending = [b for b in bridge_rows if b["sending"]]
    if any(b["state"] in ("error", "stale") for b in sending) or telegram.get("state") == "error":
        return "warn"
    return "ok"


def snapshot(db: Session, settings: AccountSettings, monitor: InternetMonitor, *, force: bool = False) -> dict:
    now = local_now(settings)
    internet = monitor.status(now, force=force)
    bridge_rows = bridges(db, settings, now)
    telegram = telegram_status(db)
    return {
        "internet": {
            "online": internet.online,
            "checked_at": internet.at.isoformat() if internet.at else None,
        },
        "bridges": bridge_rows,
        "telegram": telegram,
        "level": overall_level(internet.online, bridge_rows, telegram),
        "at": now.isoformat(),
    }
