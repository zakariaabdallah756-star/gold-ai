"""Map a trade's entry time to a market session (pure functions).

Session windows are configured in UTC. Trade times are naive wall-clock
times in the account time zone, so they are localised and converted to UTC
before matching - this keeps classification correct across DST changes.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime, timezone
from typing import Protocol
from zoneinfo import ZoneInfo

MINUTES_PER_DAY = 24 * 60


class SessionWindow(Protocol):
    name: str
    start_utc: str
    end_utc: str
    active: bool


def parse_hhmm(value: str) -> int:
    hours, minutes = value.split(":")
    return int(hours) * 60 + int(minutes)


def to_utc_minutes(local_time: datetime, tz_name: str) -> int:
    """Minutes after UTC midnight for a naive local time (or an aware one)."""
    aware = local_time if local_time.tzinfo else local_time.replace(tzinfo=ZoneInfo(tz_name))
    utc = aware.astimezone(timezone.utc)
    return utc.hour * 60 + utc.minute


def minutes_since_open(window: SessionWindow, utc_minutes: int) -> int | None:
    """Minutes elapsed since ``window`` opened, or ``None`` if it is closed."""
    start, end = parse_hhmm(window.start_utc), parse_hhmm(window.end_utc)
    length = (end - start) % MINUTES_PER_DAY
    elapsed = (utc_minutes - start) % MINUTES_PER_DAY
    return elapsed if elapsed < length else None


def classify_session(
    local_time: datetime,
    tz_name: str,
    windows: Iterable[SessionWindow],
) -> str:
    """Name of the session for ``local_time``; '' when no session is open.

    When sessions overlap (e.g. London/New York) the most recently opened one
    wins, because the newest session's liquidity usually drives the move.
    """
    utc_minutes = to_utc_minutes(local_time, tz_name)
    best_name, best_elapsed = "", None
    for window in windows:
        elapsed = minutes_since_open(window, utc_minutes)
        if elapsed is not None and (best_elapsed is None or elapsed < best_elapsed):
            best_name, best_elapsed = window.name, elapsed
    return best_name


def open_sessions(local_time: datetime, tz_name: str, windows: Iterable[SessionWindow]) -> list[str]:
    """Names of every session open at ``local_time`` (overlaps included)."""
    utc_minutes = to_utc_minutes(local_time, tz_name)
    return [window.name for window in windows if minutes_since_open(window, utc_minutes) is not None]


def in_trading_window(local_time: datetime, tz_name: str, windows: Iterable[SessionWindow]) -> bool | None:
    """Whether ``local_time`` falls in an *active* session; ``None`` if none are active."""
    active = [window for window in windows if window.active]
    if not active:
        return None
    utc_minutes = to_utc_minutes(local_time, tz_name)
    return any(minutes_since_open(window, utc_minutes) is not None for window in active)
