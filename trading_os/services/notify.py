"""Notifications: deliver a short message to the user's phone / browser.

Contract used by the rest of the app (keep these signatures):

* ``Note`` - what to send. ``event`` is one of ``EVENTS``; ``title`` and ``body``
  are plain text (no markup); ``url`` is an in-app path such as
  ``/#/alerts?id=12`` (made absolute with ``AccountSettings.app_url`` when a
  channel needs a full link); ``priority`` is low | default | high.
* ``send(db, settings, note)`` delivers ``note`` synchronously to every enabled
  channel subscribed to its event, records a ``NotificationLog`` row and returns
  it. It returns None without sending when notifications are off or the event
  type is disabled - except ``test``, which is always sent. With
  ``AccountSettings.notify_quiet_outside_sessions`` on, strategy alerts and news
  reminders are not sent while no active session is open: the row is logged with
  status ``held`` (risk events always go out). It never raises because a channel
  failed; failures are recorded per channel in the log row.
* ``register_jobs(scheduler, config)`` registers background watchers (risk
  events, news reminders) on the app's ``services.scheduler.Scheduler``.

Channels are contacted in parallel on a small thread pool. Worker threads only
see plain data; channel rows and the log are updated on the caller's thread.
"""

from __future__ import annotations

import json
import logging
import math
import threading
from concurrent.futures import ThreadPoolExecutor, wait
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from ..models import AccountSettings, Market, NewsEvent, NotificationChannel, NotificationLog, TradingSession
from . import account, news, notify_channels, webpush
from .notify_channels import DeliveryError, Message, SubscriptionExpired, VapidKeys, truncate
from .sessions import in_trading_window

log = logging.getLogger("trading_os.notify")

EVENTS = ("strategy_alert", "risk", "news", "test")
USER_EVENTS = ("strategy_alert", "risk", "news")  # the ones a user can switch on and off
QUIET_EVENTS = ("strategy_alert", "news")  # held outside the trading window when quiet hours are on
EVENT_LABELS = {
    "strategy_alert": "Strategy alerts",
    "risk": "Risk events",
    "news": "News reminders",
    "test": "Test",
}
EVENT_DESCRIPTIONS = {
    "strategy_alert": "A strategy's conditions lined up - symbol, direction, confluences, entry, SL and TP.",
    "risk": "Daily loss limit close or reached, max trades reached, kill switch, prop-firm rule breaches.",
    "news": "A reminder shortly before economic releases that meet your news impact threshold.",
}
PRIORITIES = ("low", "default", "high")
MAX_WORKERS = 4

RISK_JOB = "risk-watch"
NEWS_JOB = "news-reminders"
JOB_NAMES = (RISK_JOB, NEWS_JOB)
WATCH_INTERVAL_SEC = 60.0

RISK_TITLES = {
    "DAILY_LOSS_LIMIT": "Daily loss limit reached",
    "MAX_TRADES_REACHED": "Max trades for today reached",
    "KILL_SWITCH": "Kill switch is on",
    "PROP_DAILY_LOSS": "Prop challenge: daily loss limit hit",
    "PROP_MAX_DRAWDOWN": "Prop challenge: max drawdown hit",
    "PROP_CHALLENGE_ENDED": "Prop challenge ended",
}
PROP_CODES = frozenset({"PROP_DAILY_LOSS", "PROP_MAX_DRAWDOWN", "PROP_CHALLENGE_ENDED"})
LOSS_WARNING_CODE = "DAILY_LOSS_WARNING"

BROWSERS = (
    ("Edg/", "Edge"),
    ("OPR/", "Opera"),
    ("SamsungBrowser", "Samsung Internet"),
    ("FxiOS", "Firefox"),
    ("Firefox/", "Firefox"),
    ("CriOS", "Chrome"),
    ("Chrome/", "Chrome"),
    ("Safari/", "Safari"),
)
SYSTEMS = (
    ("iPhone", "iPhone"),
    ("iPad", "iPad"),
    ("Android", "Android"),
    ("CrOS", "ChromeOS"),
    ("Windows", "Windows"),
    ("Macintosh", "macOS"),
    ("Linux", "Linux"),
)


@dataclass
class Note:
    event: str
    title: str
    body: str
    url: str = ""
    priority: str = "default"  # low | default | high
    data: dict = field(default_factory=dict)
    tags: list[str] = field(default_factory=list)


# --------------------------------------------------------------------- send


def event_enabled(settings: AccountSettings, event: str) -> bool:
    return bool(settings.notifications_enabled) and event in (settings.notify_events or [])


def outside_trading_window(db: Session, settings: AccountSettings) -> bool:
    """True while no active session is open (False when no session is marked active)."""
    windows = db.scalars(select(TradingSession)).all()
    return in_trading_window(account.local_now(settings), settings.timezone, windows) is False


def send(db: Session, settings: AccountSettings, note: Note) -> NotificationLog | None:
    """Deliver ``note`` to every enabled channel subscribed to its event (see module docstring)."""
    if note.event != "test" and not event_enabled(settings, note.event):
        return None
    if (
        note.event in QUIET_EVENTS
        and getattr(settings, "notify_quiet_outside_sessions", False)
        and outside_trading_window(db, settings)
    ):
        return _held(db, settings, note)
    channels = db.scalars(
        select(NotificationChannel).where(NotificationChannel.enabled.is_(True)).order_by(NotificationChannel.id)
    ).all()
    targets = [c for c in channels if note.event == "test" or not c.events or note.event in c.events]
    return _dispatch(db, settings, note, targets)


def send_to_channel(
    db: Session, settings: AccountSettings, channel: NotificationChannel, note: Note
) -> NotificationLog:
    """Deliver ``note`` to one channel, whatever its switches (used by "Test")."""
    return _dispatch(db, settings, note, [channel])


def test_note() -> Note:
    return Note(
        event="test",
        title="Test notification",
        body="Trading OS can reach you here. Strategy alerts, risk events and news reminders will arrive the same way.",
        url="/#/plan",
    )


def _json_safe(data: dict) -> dict:
    return json.loads(json.dumps(data or {}, default=str))


def _message(settings: AccountSettings, note: Note) -> Message:
    path = note.url if not note.url or note.url.startswith("/") else f"/{note.url}"
    return Message(
        event=note.event,
        title=truncate(note.title, 200),
        body=note.body.strip(),
        path=path,
        link=f"{settings.app_url}{path}" if settings.app_url and path else "",
        priority=note.priority if note.priority in PRIORITIES else "default",
        tags=[str(tag) for tag in note.tags],
        data=_json_safe(note.data),
        sent_at=datetime.now(ZoneInfo(settings.timezone)).isoformat(timespec="seconds"),
    )


def _vapid(settings: AccountSettings) -> VapidKeys | None:
    if not settings.vapid_private_key or not settings.vapid_public_key:
        return None
    return VapidKeys(settings.vapid_private_key, settings.vapid_public_key, settings.vapid_contact)


def _deliver_all(jobs: list[tuple[int, str, dict]], message: Message, vapid: VapidKeys | None) -> dict:
    """Run every delivery on the pool; returns ``{channel_id: (error or None, expired)}``."""
    if not jobs:
        return {}
    workers = min(MAX_WORKERS, len(jobs))
    deadline = notify_channels.HTTP_TIMEOUT_SEC * math.ceil(len(jobs) / workers) + 5
    pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="notify")
    futures = {
        pool.submit(notify_channels.deliver, kind, config, message, vapid): (channel_id, kind)
        for channel_id, kind, config in jobs
    }
    done, _pending = wait(futures, timeout=deadline)
    pool.shutdown(wait=False, cancel_futures=True)
    outcomes = {}
    for future, (channel_id, kind) in futures.items():
        if future not in done:
            outcomes[channel_id] = ("Timed out", False)
            continue
        exc = future.exception()
        if exc is None:
            outcomes[channel_id] = (None, False)
        elif isinstance(exc, DeliveryError):
            outcomes[channel_id] = (str(exc), isinstance(exc, SubscriptionExpired))
        else:  # a bug, not a delivery problem: keep the type only (messages may carry config values)
            log.error("Notification channel %s (%s) crashed: %s", channel_id, kind, type(exc).__name__)
            outcomes[channel_id] = (f"Unexpected error ({type(exc).__name__})", False)
    return outcomes


def _status(results: list[dict]) -> str:
    if not results:
        return "skipped"
    delivered = sum(1 for result in results if result["ok"])
    if delivered == len(results):
        return "sent"
    return "partial" if delivered else "failed"


def _held(db: Session, settings: AccountSettings, note: Note) -> NotificationLog:
    """Log ``note`` as held (outside the trading window) without contacting any channel."""
    message = _message(settings, note)
    row = NotificationLog(
        created_at=account.local_now(settings),
        event=note.event,
        title=message.title,
        body=message.body,
        data=message.data,
        url=message.path[:300],
        status="held",
        results=[],
    )
    db.add(row)
    db.commit()
    return row


def _dispatch(db: Session, settings: AccountSettings, note: Note, channels: list) -> NotificationLog:
    now = account.local_now(settings)
    message = _message(settings, note)
    vapid = _vapid(settings) if any(channel.kind == "webpush" for channel in channels) else None
    outcomes = _deliver_all([(c.id, c.kind, dict(c.config or {})) for c in channels], message, vapid)
    results = []
    for channel in channels:
        error, expired = outcomes[channel.id]
        if error is None:
            channel.last_sent_at = now
            channel.last_error = ""
        else:
            log.info("Notification to channel %s (%s) failed: %s", channel.id, channel.kind, error)
            channel.last_error = error[:300]
            if expired:
                channel.enabled = False
        results.append(
            {
                "channel_id": channel.id,
                "kind": channel.kind,
                "name": channel.name,
                "ok": error is None,
                "error": error or "",
            }
        )
    row = NotificationLog(
        created_at=now,
        event=note.event,
        title=message.title,
        body=message.body,
        data=message.data,
        url=message.path[:300],
        status=_status(results),
        results=results,
    )
    db.add(row)
    db.commit()
    return row


# ------------------------------------------------------------------ Web Push


_vapid_lock = threading.Lock()


def ensure_vapid_keys(db: Session, settings: AccountSettings) -> str:
    """The VAPID public key, generating the key pair once (two first requests must not create two pairs)."""
    if settings.vapid_public_key and settings.vapid_private_key:
        return settings.vapid_public_key
    with _vapid_lock:
        db.refresh(settings)
        if not settings.vapid_public_key or not settings.vapid_private_key:
            settings.vapid_private_key, settings.vapid_public_key = webpush.generate_vapid_keys()
            db.commit()
    return settings.vapid_public_key


def device_label(user_agent: str) -> str:
    """ "Chrome on Android"-style name for a browser subscription."""
    browser = next((name for marker, name in BROWSERS if marker in user_agent), "Browser")
    system = next((name for marker, name in SYSTEMS if marker in user_agent), "")
    return f"{browser} on {system}" if system else browser


def _webpush_channels(db: Session) -> list[NotificationChannel]:
    return list(db.scalars(select(NotificationChannel).where(NotificationChannel.kind == "webpush")).all())


def upsert_webpush(
    db: Session, subscription: dict, name: str, old_endpoint: str = "", default_name: str = "Browser"
) -> NotificationChannel:
    """Save a browser subscription, updating the channel with the same (or the replaced) endpoint.

    A rotation by the browser (``old_endpoint`` set, no ``name``) keeps the channel's name and its
    on/off state; turning the device on from the page enables it. ``name`` renames explicitly.
    """
    config = notify_channels.validate("webpush", subscription)
    matches = {config["endpoint"]} | ({old_endpoint} if old_endpoint else set())
    channel = next((c for c in _webpush_channels(db) if (c.config or {}).get("endpoint") in matches), None)
    if channel is None:
        channel = NotificationChannel(kind="webpush", name=name or default_name, events=[], enabled=True)
        db.add(channel)
    else:
        if name:
            channel.name = name
        if not old_endpoint:
            channel.enabled = True
    channel.config = config
    channel.last_error = ""
    db.commit()
    return channel


def remove_webpush(db: Session, endpoint: str) -> int:
    removed = 0
    for channel in _webpush_channels(db):
        if (channel.config or {}).get("endpoint") == endpoint:
            db.delete(channel)
            removed += 1
    db.commit()
    return removed


# ------------------------------------------------------------------ watchers


def _money(amount: float, currency: str) -> str:
    return f"{amount:,.2f} {currency}"


def risk_notes(status: dict) -> list[Note]:
    """Notes for the risk events in a ``daily_status`` result, one per code."""
    notes = []
    for reason in status.get("reasons", []):
        code = reason.get("code")
        if code not in RISK_TITLES:
            continue
        block = reason.get("severity") == "block"
        notes.append(
            Note(
                event="risk",
                title=RISK_TITLES[code],
                body=reason.get("message", ""),
                url="/#/prop" if code in PROP_CODES else "/#/dashboard",
                priority="high" if block else "default",
                data={"code": code, "severity": reason.get("severity", "")},
                tags=["warning"],
            )
        )
    if status.get("state") == "warning":
        currency = status.get("currency", "")
        body = (
            f"You have used {status.get('loss_used_ratio', 0):.0%} of today's loss limit "
            f"({_money(status.get('loss_used', 0), currency)} of {_money(status.get('daily_loss_limit', 0), currency)}). "
            "Consider stopping for the day."
        )
        notes.append(
            Note(
                event="risk",
                title="Approaching your daily loss limit",
                body=body,
                url="/#/dashboard",
                data={"code": LOSS_WARNING_CODE, "severity": "warn"},
                tags=["warning"],
            )
        )
    return notes


RETRY_AFTER = timedelta(minutes=10)  # a failed or undeliverable notice is tried again after this
HANDLED_STATUSES = ("sent", "partial", "held")  # never repeated within the dedupe period


def _logged_values(db: Session, event: str, key: str, since: datetime, now: datetime) -> set:
    """Values of ``data[key]`` already handled for ``event`` since ``since`` (dedupe without extra columns).

    Delivered rows (sent / partial) and held rows count for the whole period; failed or skipped attempts
    only for ``RETRY_AFTER``, so a notice is retried when the network or a channel comes back.
    """
    rows = db.execute(
        select(NotificationLog.data, NotificationLog.status, NotificationLog.created_at).where(
            NotificationLog.event == event, NotificationLog.created_at >= since
        )
    )
    return {
        data.get(key)
        for data, status, created in rows
        if isinstance(data, dict) and (status in HANDLED_STATUSES or created >= now - RETRY_AFTER)
    }


def risk_watch(session_factory: sessionmaker[Session]) -> dict:
    """Job: notify once per account-local day for each active risk event."""
    with session_factory() as db:
        settings = account.get_settings(db)
        if not event_enabled(settings, "risk"):
            return {"sent": 0}
        notes = risk_notes(account.daily_status(db, settings))
        now = account.local_now(settings)
        today_start, _end = account.day_bounds(now.date())
        already = _logged_values(db, "risk", "code", today_start, now)
        sent = 0
        for note in notes:
            if note.data["code"] not in already:
                send(db, settings, note)
                sent += 1
        return {"sent": sent}


def _guardrail_text(event: NewsEvent, settings: AccountSettings) -> str:
    before, after = news.window_minutes(event, settings)
    start, end = news.event_window(event, settings)
    if settings.news_mode == "off":
        return "The news guardrail is off."
    effect = "are blocked" if settings.news_mode == "block" else "get a warning"
    return f"New entries {effect} from {start:%H:%M} to {end:%H:%M} ({before} min before to {after} min after)."


def _relevant(event: NewsEvent, settings: AccountSettings, markets: list[Market]) -> bool:
    if not settings.news_match_currency or event.currency == news.ALL_CURRENCIES or not markets:
        return True
    return any(news.affects_symbol(event.currency, m.symbol, m) for m in markets)


def news_notes(events: list[NewsEvent], settings: AccountSettings, markets: list[Market], now: datetime) -> list[Note]:
    """Reminders for qualifying releases that start within ``news_reminder_min`` minutes."""
    horizon = now + timedelta(minutes=settings.news_reminder_min)
    notes = []
    for event in sorted(events, key=lambda e: (e.time, e.id or 0)):
        if not (now <= event.time <= horizon) or not news.impact_meets(event.impact, settings.news_min_impact):
            continue
        if not _relevant(event, settings, markets):
            continue
        minutes = max(1, math.ceil((event.time - now).total_seconds() / 60))
        notes.append(
            Note(
                event="news",
                title=f"{event.currency} news in {minutes} min: {event.title}",
                body=f"{event.title} ({event.currency}, {event.impact} impact) at {event.time:%H:%M}. "
                + _guardrail_text(event, settings),
                url="/#/premarket",
                data={"news_event_id": event.id, "currency": event.currency, "impact": event.impact},
                tags=["calendar"],
            )
        )
    return notes


def news_reminders(session_factory: sessionmaker[Session]) -> dict:
    """Job: one reminder per qualifying release, ``news_reminder_min`` minutes ahead."""
    with session_factory() as db:
        settings = account.get_settings(db)
        if not event_enabled(settings, "news") or settings.news_reminder_min <= 0:
            return {"sent": 0}
        now = account.local_now(settings)
        events = db.scalars(
            select(NewsEvent).where(
                NewsEvent.time >= now, NewsEvent.time <= now + timedelta(minutes=settings.news_reminder_min)
            )
        ).all()
        markets = list(db.scalars(select(Market).where(Market.active.is_(True))).all())
        reminded = _logged_values(db, "news", "news_event_id", now - timedelta(days=1), now)
        sent = 0
        for note in news_notes(list(events), settings, markets, now):
            if note.data["news_event_id"] not in reminded:
                send(db, settings, note)
                sent += 1
        return {"sent": sent}


def register_jobs(scheduler, config) -> None:
    """Register the risk and news watchers (each returns at once while its event is off)."""
    scheduler.register(
        RISK_JOB,
        WATCH_INTERVAL_SEC,
        risk_watch,
        description="Checks the daily loss limit, max trades, kill switch and prop rules every minute.",
        initial_delay=15.0,
    )
    scheduler.register(
        NEWS_JOB,
        WATCH_INTERVAL_SEC,
        news_reminders,
        description="Sends a reminder before qualifying economic releases.",
        initial_delay=20.0,
    )
