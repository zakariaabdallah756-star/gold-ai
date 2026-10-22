"""Post-market daily reviews: storage, the day's trading stats and the review history.

A day's P&L is the P&L *booked* that day - each fill of a managed trade on its
fill day (partial exits included), any other trade on the day it closed - the
same attribution as the P&L calendar and daily P&L. Trade counts, win rate,
compliance, mistakes and emotions come from the trades *closed* that day.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping, Sequence
from datetime import date, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from ..models import ChecklistDay, DailyReview, Trade
from .account import CLOSE_TIME, booked_clause, day_bounds
from .analytics import Booking, bookings

REVIEW_FIELDS = (
    "discipline_rating",
    "mood",
    "followed_plan",
    "went_well",
    "went_wrong",
    "lessons",
    "tomorrow_focus",
    "mistakes",
)
EMPTY_REVIEW = {
    "discipline_rating": None,
    "mood": "",
    "followed_plan": None,
    "went_well": "",
    "went_wrong": "",
    "lessons": "",
    "tomorrow_focus": "",
    "mistakes": [],
}


def review_fields(record: DailyReview | None) -> dict:
    """The editable fields of a review (blank values when there is none yet)."""
    if record is None:
        return {**EMPTY_REVIEW, "mistakes": []}
    values = {name: getattr(record, name) for name in REVIEW_FIELDS}
    values["mistakes"] = list(record.mistakes or [])
    return values


def booked_between(db: Session, first: date, last: date, load=(selectinload(Trade.events),)) -> list[Trade]:
    """Trades (open or closed) with P&L booked in ``[first, last]``, in close order, ``load`` options applied."""
    start, _ = day_bounds(first)
    _, end = day_bounds(last)
    stmt = select(Trade).where(booked_clause(start, end)).options(*load)
    return list(db.scalars(stmt.order_by(CLOSE_TIME.asc(), Trade.id.asc())))


def bookings_between(trades: Iterable[Trade], first: date, last: date) -> list[Booking]:
    """The bookings of ``trades`` dated in ``[first, last]``, in time order."""
    start, _ = day_bounds(first)
    _, end = day_bounds(last)
    return bookings(trades, since=start, before=end)


def _ranked(counter: Counter) -> list[dict]:
    return [{"name": name, "count": count} for name, count in sorted(counter.items(), key=lambda kv: (-kv[1], kv[0]))]


def day_stats(booked: Sequence[Booking], opened: int) -> dict:
    """Summary of a day's bookings; ``opened`` is the number of trades entered that day.

    ``net_pnl`` is everything booked that day, ``partials`` the fills that did
    not close their trade; the other figures describe the trades closed that day.
    """
    closed = [b.trade for b in booked if b.closes]
    count = len(closed)
    wins = sum(1 for t in closed if t.outcome == "win")
    rated = [t for t in closed if t.rule_compliant is not None]
    followed = sum(1 for t in rated if t.rule_compliant)
    return {
        "trades": count,
        "net_pnl": round(sum(b.amount for b in booked), 2),
        "partials": len(booked) - count,
        "wins": wins,
        "losses": sum(1 for t in closed if t.outcome == "loss"),
        "win_rate": round(wins / count * 100, 2) if count else None,
        "compliance_rate": round(followed / len(rated) * 100, 2) if rated else None,
        "mistakes": _ranked(Counter(name for t in closed for name in t.mistakes or [])),
        "emotions": _ranked(Counter(t.emotion for t in closed if t.emotion)),
        "opened": opened,
    }


def _opened_on(db: Session, day: date) -> int:
    start, end = day_bounds(day)
    return db.scalar(select(func.count(Trade.id)).where(Trade.entry_time >= start, Trade.entry_time < end)) or 0


def get_review(db: Session, day: date) -> dict:
    record = db.get(DailyReview, day)
    return {
        "day": day,
        "exists": record is not None,
        **review_fields(record),
        "stats": day_stats(bookings_between(booked_between(db, day, day), day, day), _opened_on(db, day)),
    }


def save_review(db: Session, day: date, data: Mapping) -> dict:
    """Create or replace the review of ``day`` with the validated ``data``."""
    record = db.get(DailyReview, day) or DailyReview(day=day)
    for name in REVIEW_FIELDS:
        setattr(record, name, list(data[name]) if name == "mistakes" else data[name])
    db.add(record)
    db.commit()
    return get_review(db, day)


def history(db: Session, today: date, days: int) -> list[dict]:
    """One row per day for the ``days`` days up to and including ``today``, newest first."""
    first = today - timedelta(days=days - 1)
    records = {r.day: r for r in db.scalars(select(DailyReview).where(DailyReview.day.between(first, today)))}
    by_day: dict[date, list[Booking]] = defaultdict(list)
    for booking in bookings_between(booked_between(db, first, today), first, today):
        by_day[booking.time.date()].append(booking)

    rows = []
    for offset in range(days):
        day = today - timedelta(days=offset)
        record = records.get(day)
        booked = by_day.get(day, [])
        closed = sum(1 for b in booked if b.closes)
        rows.append(
            {
                "day": day,
                "exists": record is not None,
                "discipline_rating": record.discipline_rating if record else None,
                "mood": record.mood if record else "",
                "followed_plan": record.followed_plan if record else None,
                "pnl": round(sum(b.amount for b in booked), 2),
                "trades": closed,
                "partials": len(booked) - closed,
            }
        )
    return rows


def reviewed_days(db: Session, first: date, last: date) -> set[date]:
    return set(db.scalars(select(DailyReview.day).where(DailyReview.day.between(first, last))))


def checklist_completed_days(db: Session, first: date, last: date) -> set[date]:
    """Days in ``[first, last]`` whose pre-market checklist was completed (as it stood that day)."""
    records: Iterable[ChecklistDay] = db.scalars(select(ChecklistDay).where(ChecklistDay.day.between(first, last)))
    return {r.day for r in records if r.completed_at is not None}
