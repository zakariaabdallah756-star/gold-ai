"""Context guardrails: economic-calendar windows and the emergency kill switch.

Hook points used by the rest of the app (signatures are a contract):

* ``entry_violations`` is called by ``services.trades.evaluate`` for every
  *new* trade. Return ``Violation`` objects with the severity the account's
  settings prescribe; ``evaluate`` downgrades them to warnings for trades that
  are not live entries.
* ``status_extras`` is merged into ``/api/risk/status`` by
  ``services.account.daily_status``.

The rules themselves are pure functions in ``services.news``; this module only
loads the events that can matter for a given moment.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import AccountSettings, Market, NewsEvent
from . import news
from .guardrails import Violation


def events_between(db: Session, start: datetime, end: datetime) -> list[NewsEvent]:
    """News events released within ``[start, end]``, in chronological order."""
    stmt = (
        select(NewsEvent).where(NewsEvent.time >= start, NewsEvent.time <= end).order_by(NewsEvent.time, NewsEvent.id)
    )
    return list(db.scalars(stmt))


def entry_violations(
    db: Session,
    settings: AccountSettings,
    *,
    symbol: str,
    entry_time: datetime,
    now: datetime,
) -> list[Violation]:
    """NEWS_WINDOW violations for ``symbol`` at ``entry_time`` plus KILL_SWITCH while a cool-off runs."""
    violations: list[Violation] = []
    if settings.news_mode != "off":
        margin = news.search_margin(settings)
        events = events_between(db, entry_time - margin, entry_time + margin)
        if events:
            market = db.scalar(select(Market).where(Market.symbol == symbol))
            violations += news.entry_news_violations(
                events, settings, symbol=symbol, market=market, entry_time=entry_time
            )
    if news.cooloff_covers(settings, entry_time, now):
        violations.append(news.cooloff_violation(settings))
    return violations


def _open_windows(db: Session, settings: AccountSettings, events: list[NewsEvent], now: datetime) -> list[Violation]:
    """NEWS_WINDOW status reasons, judged against the markets the account actually trades.

    With currency matching on, a release that affects none of the active markets is left out, and one
    that affects only some of them names those markets (entries in the others are not restricted).
    """
    if settings.news_mode == "off":
        return []
    markets = list(db.scalars(select(Market).where(Market.active.is_(True))))
    severity = news.severity_for(settings.news_mode)
    reasons = []
    for event in events:
        if not news.impact_meets(event.impact, settings.news_min_impact) or not news.in_window(event, settings, now):
            continue
        message = news.window_message(event, settings)
        if settings.news_match_currency and event.currency != news.ALL_CURRENCIES and markets:
            affected = [m.symbol for m in markets if news.affects_symbol(event.currency, m.symbol, m)]
            if not affected:
                continue
            if len(affected) < len(markets):
                message = f"{message} Affects {', '.join(affected)}."
        reasons.append(Violation("NEWS_WINDOW", message, severity))
    return reasons


def status_extras(db: Session, settings: AccountSettings, now: datetime) -> dict:
    """Status fields: ``reasons`` (list[Violation]), ``cooloff_until``, ``cooloff_reason``, ``upcoming_news``."""
    events = events_between(db, now - news.search_margin(settings), now + news.UPCOMING_HORIZON)
    active = news.cooloff_active(settings, now)
    reasons = [news.cooloff_violation(settings)] if active else []
    reasons += _open_windows(db, settings, events, now)
    return {
        "reasons": reasons,
        "cooloff_until": settings.cooloff_until if active else None,
        "cooloff_reason": settings.cooloff_reason if active else "",
        "upcoming_news": news.upcoming(events, settings, now),
    }
