"""Economic calendar (news events) and the emergency kill switch.

Event times are naive account-local datetimes (aware ones are converted). Every
event is returned with its effective restriction window (the event's own
before/after minutes or the account defaults) and whether that window is open.

The kill switch starts a cool-off that blocks every new live entry. While it
runs it can only be extended; ending it early requires typing ``UNLOCK``.
Cool-off fields are deliberately absent from ``PUT /api/settings``.
"""

import http.client
import json
import urllib.request
from datetime import date, datetime, time, timedelta
from typing import Annotated, Literal

from fastapi import APIRouter, HTTPException, Query, Response
from pydantic import BaseModel, BeforeValidator, Field, StringConstraints
from sqlalchemy import select

from .. import APP_NAME, __version__
from ..models import AccountSettings, NewsEvent
from ..schemas import DailyStatusOut
from ..services import account
from ..services import news as news_rules
from ..services.trades import normalize_time
from .deps import DbDep, SettingsDep, get_or_404

router = APIRouter(prefix="/api", tags=["news"])

FOREXFACTORY_URL = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
FETCH_TIMEOUT_S = 10
UNLOCK_PHRASE = "UNLOCK"


def _upper(value):
    return value.strip().upper() if isinstance(value, str) else value


Impact = Literal["low", "medium", "high"]
NewsCurrency = Annotated[str, StringConstraints(pattern=r"^[A-Z]{3}$"), BeforeValidator(_upper)]
WindowMinutes = Annotated[int | None, Field(ge=0, le=news_rules.MAX_WINDOW_MIN)]


# --------------------------------------------------------------------------- schemas


class NewsIn(BaseModel):
    time: datetime
    title: Annotated[str, StringConstraints(min_length=1, max_length=news_rules.TITLE_MAX, strip_whitespace=True)]
    currency: NewsCurrency = Field("USD", description='ISO currency code, or "ALL" for every market')
    impact: Impact = "high"
    before_min: WindowMinutes = None
    after_min: WindowMinutes = None
    notes: Annotated[str, StringConstraints(max_length=news_rules.NOTES_MAX, strip_whitespace=True)] = ""


class NewsOut(BaseModel):
    id: int
    time: datetime
    title: str
    currency: str
    impact: str
    before_min: int | None
    after_min: int | None
    notes: str
    before: int  # effective minutes (override or account default)
    after: int
    window_start: datetime
    window_end: datetime
    active: bool  # window open right now
    meets_threshold: bool  # impact >= the account's minimum impact


class ImportIn(BaseModel):
    min_impact: Impact | None = Field(None, description="Lowest impact to import (default: the account minimum)")


class ImportOut(BaseModel):
    fetched: int
    imported: int
    duplicates: int
    below_threshold: int


class KillSwitchIn(BaseModel):
    hours: int = Field(24, ge=1, le=72)
    reason: Annotated[str, StringConstraints(max_length=200, strip_whitespace=True)] = ""


class ReleaseIn(BaseModel):
    confirm: str = ""


# --------------------------------------------------------------------------- helpers


def _out(event: NewsEvent, settings: AccountSettings, now: datetime) -> NewsOut:
    return NewsOut(
        id=event.id,
        time=event.time,
        title=event.title,
        currency=event.currency,
        impact=event.impact,
        before_min=event.before_min,
        after_min=event.after_min,
        notes=event.notes,
        **news_rules.window_state(event, settings, now),
    )


def _apply(event: NewsEvent, payload: NewsIn, settings: AccountSettings) -> None:
    data = payload.model_dump()
    data["time"] = normalize_time(payload.time, settings.timezone).replace(second=0)
    for key, value in data.items():
        setattr(event, key, value)


def _fetch_forexfactory() -> object:
    """Download this week's ForexFactory calendar (raises ``OSError``, ``ValueError`` or
    ``http.client.HTTPException`` on failure)."""
    request = urllib.request.Request(
        FOREXFACTORY_URL,
        headers={"User-Agent": f"{APP_NAME}/{__version__}", "Accept": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=FETCH_TIMEOUT_S) as response:
        return json.loads(response.read().decode("utf-8"))


# --------------------------------------------------------------------------- news


@router.get("/news", response_model=list[NewsOut])
def list_news(
    settings: SettingsDep,
    db: DbDep,
    date_from: Annotated[date | None, Query(description="First day (default: yesterday)")] = None,
    date_to: Annotated[date | None, Query(description="Last day, inclusive (default: today + 7 days)")] = None,
):
    now = account.local_now(settings)
    first = date_from or now.date() - timedelta(days=1)
    last = date_to or now.date() + timedelta(days=7)
    if last < first:
        raise HTTPException(status_code=422, detail="date_to must not be before date_from")
    stmt = (
        select(NewsEvent)
        .where(
            NewsEvent.time >= datetime.combine(first, time.min),
            NewsEvent.time < datetime.combine(last + timedelta(days=1), time.min),
        )
        .order_by(NewsEvent.time, NewsEvent.id)
    )
    return [_out(event, settings, now) for event in db.scalars(stmt)]


@router.post("/news", response_model=NewsOut, status_code=201)
def create_news(payload: NewsIn, settings: SettingsDep, db: DbDep):
    event = NewsEvent()
    _apply(event, payload, settings)
    db.add(event)
    db.commit()
    return _out(event, settings, account.local_now(settings))


@router.put("/news/{event_id}", response_model=NewsOut)
def update_news(event_id: int, payload: NewsIn, settings: SettingsDep, db: DbDep):
    event = get_or_404(db, NewsEvent, event_id)
    _apply(event, payload, settings)
    db.commit()
    return _out(event, settings, account.local_now(settings))


@router.delete("/news/{event_id}", status_code=204)
def delete_news(event_id: int, db: DbDep):
    db.delete(get_or_404(db, NewsEvent, event_id))
    db.commit()
    return Response(status_code=204)


@router.post("/news/import-forexfactory", response_model=ImportOut)
def import_forexfactory(settings: SettingsDep, db: DbDep, payload: ImportIn | None = None):
    """Import this week's ForexFactory calendar; events already in the calendar are skipped."""
    try:
        feed = _fetch_forexfactory()
    except (OSError, ValueError, http.client.HTTPException) as exc:  # URLError/HTTPError/timeouts are OSErrors
        reason = getattr(exc, "reason", None) or exc
        raise HTTPException(status_code=502, detail=f"Could not download the ForexFactory calendar ({reason})") from exc

    parsed = news_rules.parse_forexfactory(feed, settings.timezone)
    minimum = (payload.min_impact if payload else None) or settings.news_min_impact
    wanted = [item for item in parsed if news_rules.impact_meets(item["impact"], minimum)]
    existing: set[tuple[str, str, datetime]] = set()
    if wanted:
        times = [item["time"] for item in wanted]
        stmt = select(NewsEvent).where(NewsEvent.time >= min(times), NewsEvent.time <= max(times))
        existing = {(e.title, e.currency, e.time) for e in db.scalars(stmt)}
    fresh = [item for item in wanted if (item["title"], item["currency"], item["time"]) not in existing]
    db.add_all(NewsEvent(**item) for item in fresh)
    db.commit()
    return ImportOut(
        fetched=len(parsed),
        imported=len(fresh),
        duplicates=len(wanted) - len(fresh),
        below_threshold=len(parsed) - len(wanted),
    )


# --------------------------------------------------------------------------- kill switch


@router.post("/killswitch", response_model=DailyStatusOut)
def activate_killswitch(payload: KillSwitchIn, settings: SettingsDep, db: DbDep):
    """Start (or extend) a cool-off of ``hours`` from now. An active cool-off is never shortened."""
    now = account.local_now(settings)
    active = news_rules.cooloff_active(settings, now)
    settings.cooloff_until = news_rules.extended_cooloff(settings.cooloff_until if active else None, now, payload.hours)
    if payload.reason or not active:
        settings.cooloff_reason = payload.reason
    db.commit()
    return account.daily_status(db, settings)


@router.post("/killswitch/release", response_model=DailyStatusOut)
def release_killswitch(payload: ReleaseIn, settings: SettingsDep, db: DbDep):
    """End the cool-off early. Requires ``confirm == "UNLOCK"``."""
    if payload.confirm.strip() != UNLOCK_PHRASE:
        raise HTTPException(status_code=400, detail=f'Type "{UNLOCK_PHRASE}" to end the cool-off early')
    settings.cooloff_until = None
    settings.cooloff_reason = ""
    db.commit()
    return account.daily_status(db, settings)
