"""Daily pre-market routine: completion tracking and the trade-entry gate."""

from __future__ import annotations

from datetime import date, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import ChecklistDay, ChecklistItem
from ..schemas import ChecklistDayIn


def active_items(db: Session) -> list[ChecklistItem]:
    stmt = select(ChecklistItem).where(ChecklistItem.active.is_(True))
    return list(db.scalars(stmt.order_by(ChecklistItem.sort_order, ChecklistItem.id)))


def progress(checked_ids: list[int], items: list[ChecklistItem]) -> dict:
    """Completion figures. If no item is marked required, every item is."""
    checked = set(checked_ids)
    required = [item for item in items if item.required] or items
    required_done = sum(1 for item in required if item.id in checked)
    return {
        "total": len(items),
        "done": sum(1 for item in items if item.id in checked),
        "required_total": len(required),
        "required_done": required_done,
        "completed": required_done == len(required),
    }


def _serialize(day: date, record: ChecklistDay | None, items: list[ChecklistItem]) -> dict:
    checked = list(record.checked_item_ids) if record else []
    return {
        "day": day,
        "checked_item_ids": checked,
        "bias": record.bias if record else "",
        "key_levels": record.key_levels if record else "",
        "news": record.news if record else "",
        "notes": record.notes if record else "",
        "completed_at": record.completed_at if record else None,
        **progress(checked, items),
    }


def get_day(db: Session, day: date) -> dict:
    return _serialize(day, db.get(ChecklistDay, day), active_items(db))


def save_day(db: Session, day: date, data: ChecklistDayIn, now: datetime) -> dict:
    items = active_items(db)
    valid_ids = {item.id for item in items}
    record = db.get(ChecklistDay, day) or ChecklistDay(day=day)
    record.checked_item_ids = sorted({i for i in data.checked_item_ids if i in valid_ids})
    record.bias = data.bias
    record.key_levels = data.key_levels
    record.news = data.news
    record.notes = data.notes

    complete = progress(record.checked_item_ids, items)["completed"]
    if complete and record.completed_at is None:
        record.completed_at = now
    elif not complete:
        record.completed_at = None

    db.add(record)
    db.commit()
    return _serialize(day, record, items)


def is_complete(db: Session, day: date) -> bool:
    record = db.get(ChecklistDay, day)
    return progress(list(record.checked_item_ids) if record else [], active_items(db))["completed"]


def history(db: Session, today: date, days: int) -> list[dict]:
    """Completion status for the ``days`` days up to and including ``today``."""
    items = active_items(db)
    start = today - timedelta(days=days - 1)
    records = {
        record.day: record
        for record in db.scalars(select(ChecklistDay).where(ChecklistDay.day >= start, ChecklistDay.day <= today))
    }
    out = []
    for offset in range(days):
        day = today - timedelta(days=offset)
        record = records.get(day)
        entry = progress(list(record.checked_item_ids) if record else [], items)
        if day < today:  # judged by the checklist of that day, not today's template
            entry["completed"] = record is not None and record.completed_at is not None
        out.append({"day": day, "started": record is not None, "bias": record.bias if record else "", **entry})
    return out
