"""Economic-calendar and cool-off rules (pure functions, no I/O).

All datetimes are naive and expressed in the account time zone, like trade
times. ``event`` arguments are ``NewsEvent``-like objects (``time``, ``title``,
``currency``, ``impact``, ``before_min``, ``after_min``) and ``settings`` is an
``AccountSettings``-like object, so the rules can be tested without a database.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from .guardrails import Violation
from .i18n import N_, tr

IMPACT_RANK = {"low": 0, "medium": 1, "high": 2}
ALL_CURRENCIES = "ALL"  # pseudo-currency of events that move every market
MAX_WINDOW_MIN = 240  # upper bound of the before/after minutes (settings and per-event overrides)
UPCOMING_HORIZON = timedelta(hours=24)
IMPACT_NAMES = {"low": N_("low"), "medium": N_("medium"), "high": N_("high")}
CURRENCY_RE = re.compile(r"^[A-Z]{3}$")
TITLE_MAX = 120
NOTES_MAX = 200


# --------------------------------------------------------------------- matching


def impact_meets(impact: str, minimum: str) -> bool:
    """True when ``impact`` ranks at or above ``minimum`` (low < medium < high)."""
    return IMPACT_RANK.get(impact, -1) >= IMPACT_RANK.get(minimum, IMPACT_RANK["high"])


def window_minutes(event, settings) -> tuple[int, int]:
    """Restricted minutes (before, after) the release: the event's override or the account default."""
    before = event.before_min if event.before_min is not None else settings.news_before_min
    after = event.after_min if event.after_min is not None else settings.news_after_min
    return before, after


def event_window(event, settings) -> tuple[datetime, datetime]:
    """Restricted period ``[time - before, time + after]`` around the release."""
    before, after = window_minutes(event, settings)
    return event.time - timedelta(minutes=before), event.time + timedelta(minutes=after)


def in_window(event, settings, at: datetime) -> bool:
    """True when ``at`` falls inside the event's restricted period (bounds included)."""
    start, end = event_window(event, settings)
    return start <= at <= end


def search_margin(settings) -> timedelta:
    """How far from a moment an event can be while its window still covers that moment."""
    return timedelta(minutes=max(MAX_WINDOW_MIN, settings.news_before_min, settings.news_after_min))


def affects_symbol(currency: str, symbol: str, market=None, *, match_currency: bool = True) -> bool:
    """Whether an event in ``currency`` is relevant to ``symbol``.

    ``ALL`` events and accounts that do not match currencies affect every
    market. Otherwise the currency must be the market's base or quote
    currency; for an unconfigured symbol it must appear in the symbol text.
    """
    if currency == ALL_CURRENCIES or not match_currency:
        return True
    if market is not None:
        return currency in {market.base_currency, market.quote_currency}
    return currency in symbol.upper()


# ------------------------------------------------------------------- violations


def severity_for(mode: str) -> str:
    """Violation severity for a news mode: ``block`` blocks live entries, anything else warns."""
    return "block" if mode == "block" else "warn"


def window_message(event, settings) -> str:
    before, after = window_minutes(event, settings)
    impact = tr(IMPACT_NAMES[event.impact]) if event.impact in IMPACT_NAMES else event.impact
    return tr(
        "{title} ({currency}, {impact}) at {time:%H:%M} - "
        "entries are restricted {before} min before to {after} min after.",
        title=event.title,
        currency=event.currency,
        impact=impact,
        time=event.time,
        before=before,
        after=after,
    )


def entry_news_violations(events: Iterable, settings, *, symbol: str, market, entry_time: datetime) -> list[Violation]:
    """One NEWS_WINDOW violation per relevant event whose window contains ``entry_time``."""
    if settings.news_mode == "off":
        return []
    severity = severity_for(settings.news_mode)
    return [
        Violation("NEWS_WINDOW", window_message(event, settings), severity)
        for event in events
        if impact_meets(event.impact, settings.news_min_impact)
        and affects_symbol(event.currency, symbol, market, match_currency=settings.news_match_currency)
        and in_window(event, settings, entry_time)
    ]


def active_window_violations(events: Iterable, settings, now: datetime) -> list[Violation]:
    """NEWS_WINDOW status reasons for every qualifying event (any currency) whose window is open now."""
    if settings.news_mode == "off":
        return []
    severity = severity_for(settings.news_mode)
    return [
        Violation("NEWS_WINDOW", window_message(event, settings), severity)
        for event in events
        if impact_meets(event.impact, settings.news_min_impact) and in_window(event, settings, now)
    ]


def window_state(event, settings, now: datetime) -> dict:
    """Effective restriction window of ``event`` and whether it is open at ``now``."""
    before, after = window_minutes(event, settings)
    start, end = event_window(event, settings)
    return {
        "before": before,
        "after": after,
        "window_start": start,
        "window_end": end,
        "active": start <= now <= end,
        "meets_threshold": impact_meets(event.impact, settings.news_min_impact),
    }


def upcoming(events: Iterable, settings, now: datetime, horizon: timedelta = UPCOMING_HORIZON) -> list[dict]:
    """Qualifying events whose window has not ended yet and that are released within ``horizon``."""
    items = []
    for event in sorted(events, key=lambda e: (e.time, e.id or 0)):
        if not impact_meets(event.impact, settings.news_min_impact) or event.time > now + horizon:
            continue
        start, end = event_window(event, settings)
        if end < now:
            continue
        items.append(
            {
                "id": event.id,
                "time": event.time,
                "title": event.title,
                "currency": event.currency,
                "impact": event.impact,
                "window_start": start,
                "window_end": end,
                "active": start <= now <= end,
            }
        )
    return items


# ---------------------------------------------------------------------- cool-off


def cooloff_active(settings, now: datetime) -> bool:
    """True while the kill switch's cool-off period has not ended."""
    return settings.cooloff_until is not None and now < settings.cooloff_until


def cooloff_covers(settings, entry_time: datetime, now: datetime) -> bool:
    """Whether an entry at ``entry_time`` falls inside the running cool-off.

    Trades logged after the fact from before the cool-off started are not covered.
    """
    if not cooloff_active(settings, now):
        return False
    started = settings.cooloff_started_at
    return started is None or entry_time >= started


def cooloff_violation(settings) -> Violation:
    reason = f" ({settings.cooloff_reason})" if settings.cooloff_reason else ""
    return Violation(
        "KILL_SWITCH",
        tr(
            "Cool-off active until {until:%d %b %Y %H:%M}{reason}. No new trades.",
            until=settings.cooloff_until,
            reason=reason,
        ),
        "block",
    )


def extended_cooloff(current_until: datetime | None, now: datetime, hours: float) -> datetime:
    """End of a cool-off of ``hours`` from ``now``; an active cool-off is only ever extended."""
    target = (now + timedelta(hours=hours)).replace(microsecond=0)
    if current_until is not None and current_until > target:
        return current_until
    return target


# ------------------------------------------------------------------- ForexFactory


def parse_forexfactory(items, tz_name: str) -> list[dict]:
    """Convert the ForexFactory weekly JSON feed into ``NewsEvent`` field dicts.

    Feed items look like ``{"title", "country": "USD", "date": ISO-8601 with
    offset, "impact": "High"|"Medium"|"Low"|"Holiday", "forecast", "previous"}``.
    Times are converted to naive account-local time. Holidays, unknown impacts,
    codes that are not three letters, unparsable dates and in-feed duplicates
    (same title, currency and time) are skipped. The feed's "All" country maps
    to the ``ALL`` pseudo-currency (relevant to every market).
    """
    zone = ZoneInfo(tz_name)
    seen: set[tuple[str, str, datetime]] = set()
    events: list[dict] = []
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, dict):
            continue
        impact = str(item.get("impact") or "").strip().lower()
        currency = str(item.get("country") or "").strip().upper()
        title = " ".join(str(item.get("title") or "").split())[:TITLE_MAX]
        if impact not in IMPACT_RANK or not CURRENCY_RE.match(currency) or not title:
            continue
        try:
            when = datetime.fromisoformat(str(item.get("date") or ""))
        except ValueError:
            continue
        if when.tzinfo is not None:
            when = when.astimezone(zone)
        when = when.replace(tzinfo=None, second=0, microsecond=0)
        key = (title, currency, when)
        if key in seen:
            continue
        seen.add(key)
        figures = [
            f"{tr(label)} {str(item[field]).strip()}"
            for field, label in (("forecast", N_("Forecast")), ("previous", N_("Previous")))
            if str(item.get(field) or "").strip()
        ]
        events.append(
            {
                "time": when,
                "title": title,
                "currency": currency,
                "impact": impact,
                "notes": " · ".join(figures)[:NOTES_MAX],
            }
        )
    return events
