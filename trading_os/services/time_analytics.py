"""Time-of-day heatmap and the P&L calendar (pure functions over closed trades).

Trade times are naive datetimes on the account's wall clock, so ``entry_time.hour``
and ``weekday()`` already are the trader's local hour and weekday - no time-zone
conversion happens here.

* The **heatmap** buckets trades by the weekday (0 = Monday) and hour of *entry*:
  it answers "when do I trade well?", which is a question about the decision time.
* The **calendar** books realised P&L on the day it was realised, like the
  dashboard's daily P&L (``analytics.bookings``): each fill of a managed trade
  on its fill day - partial exits included, even while the trade is still
  open - with the fees on the closing fill, and any other closed trade on the
  day it closed. Trade counts and win rates count the trades *closed* that day;
  ``partials`` counts the fills that did not close their trade.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Collection, Iterable, Sequence
from datetime import date, datetime, time, timedelta
from statistics import fmean
from typing import Protocol

from .analytics import Booking, booking_stats, bookings
from .i18n import N_, tr

HOURS_PER_DAY = 24
DAYS_PER_WEEK = 7
WEEKDAY_LABELS = (
    N_("Monday"),
    N_("Tuesday"),
    N_("Wednesday"),
    N_("Thursday"),
    N_("Friday"),
    N_("Saturday"),
    N_("Sunday"),
)


class TimedTrade(Protocol):
    pnl: float
    outcome: str
    r_multiple: float | None
    entry_time: datetime

    @property
    def close_time(self) -> datetime: ...


def _counts(trades: Sequence[TimedTrade]) -> dict:
    count = len(trades)
    wins = sum(1 for t in trades if t.outcome == "win")
    return {
        "trades": count,
        "wins": wins,
        "losses": sum(1 for t in trades if t.outcome == "loss"),
        "win_rate": round(wins / count * 100, 2) if count else None,
    }


def _net(trades: Iterable[TimedTrade]) -> float:
    return round(sum(t.pnl or 0.0 for t in trades), 2)


def _heat_stats(trades: Sequence[TimedTrade]) -> dict:
    r_values = [t.r_multiple for t in trades if t.r_multiple is not None]
    return {
        **_counts(trades),
        "net_pnl": _net(trades),
        "avg_r": round(fmean(r_values), 2) if r_values else None,
    }


def heatmap(trades: Iterable[TimedTrade]) -> dict:
    """Weekday x entry-hour cells (only non-empty ones) plus the hour and weekday marginals."""
    cells: dict[tuple[int, int], list] = defaultdict(list)
    by_hour: dict[int, list] = defaultdict(list)
    by_weekday: dict[int, list] = defaultdict(list)
    for trade in trades:
        weekday, hour = trade.entry_time.weekday(), trade.entry_time.hour
        cells[(weekday, hour)].append(trade)
        by_hour[hour].append(trade)
        by_weekday[weekday].append(trade)
    return {
        "cells": [
            {"weekday": weekday, "hour": hour, **_heat_stats(group)} for (weekday, hour), group in sorted(cells.items())
        ],
        "by_hour": [{"hour": hour, **_heat_stats(by_hour[hour])} for hour in range(HOURS_PER_DAY)],
        "by_weekday": [
            {"weekday": day, "label": tr(WEEKDAY_LABELS[day]), **_heat_stats(by_weekday[day])}
            for day in range(DAYS_PER_WEEK)
        ],
    }


# ------------------------------------------------------------------ calendar


def parse_month(text: str) -> date:
    """``"2026-03"`` -> ``date(2026, 3, 1)``. Raises ``ValueError`` for anything else."""
    return datetime.strptime(text, "%Y-%m").date()


def month_grid(month: date) -> tuple[date, date, date, date]:
    """First and last day of ``month`` and the Monday-to-Sunday range of full weeks covering it."""
    first = month.replace(day=1)
    last = (first + timedelta(days=32)).replace(day=1) - timedelta(days=1)
    start = first - timedelta(days=first.weekday())
    end = last + timedelta(days=DAYS_PER_WEEK - 1 - last.weekday())
    return first, last, start, end


def _extreme(days: list[dict], pick) -> dict | None:
    if not days:
        return None
    day = pick(days, key=lambda d: d["pnl"])
    return {"date": day["date"], "pnl": day["pnl"], "trades": day["trades"]}


def calendar_month(
    trades: Iterable[TimedTrade],
    month: date,
    *,
    reviewed: Collection[date] = (),
    checklist_complete: Collection[date] = (),
) -> dict:
    """The month as whole Monday-Sunday weeks, with per-day, per-week and month totals.

    ``trades`` are the trades with P&L booked in the grid (open trades with
    partial exits included). ``weeks`` total the full seven days of each grid
    row (adjacent-month days included, as they are shown); ``month_total``
    covers the month's own days. A day with any booking - a closed trade or a
    partial exit - is a trading day. The best day is the most profitable green
    day and the worst day the deepest red day, so either is ``None`` when the
    month has none.
    """
    first, last, start, end = month_grid(month)
    by_day: dict[date, list[Booking]] = defaultdict(list)
    since, before = datetime.combine(start, time.min), datetime.combine(end + timedelta(days=1), time.min)
    for booking in bookings(trades, since=since, before=before):
        by_day[booking.time.date()].append(booking)

    dates = [start + timedelta(days=offset) for offset in range((end - start).days + 1)]
    days = [
        {
            "date": day.isoformat(),
            "in_month": first <= day <= last,
            **booking_stats(by_day.get(day, [])),
            "has_review": day in reviewed,
            "checklist_complete": day in checklist_complete,
        }
        for day in dates
    ]
    weeks = [
        {
            "week_start": dates[index].isoformat(),
            **booking_stats([b for day in dates[index : index + DAYS_PER_WEEK] for b in by_day.get(day, [])]),
        }
        for index in range(0, len(dates), DAYS_PER_WEEK)
    ]

    month_days = [d for d in days if d["in_month"] and (d["trades"] or d["partials"])]
    green = [d for d in month_days if d["pnl"] > 0]
    red = [d for d in month_days if d["pnl"] < 0]
    month_bookings = [b for day, group in by_day.items() if first <= day <= last for b in group]
    return {
        "month": first.strftime("%Y-%m"),
        "start": start.isoformat(),
        "end": end.isoformat(),
        "days": days,
        "weeks": weeks,
        "month_total": {
            **booking_stats(month_bookings),
            "trading_days": len(month_days),
            "green_days": len(green),
            "red_days": len(red),
            "best_day": _extreme(green, max),
            "worst_day": _extreme(red, min),
        },
    }
