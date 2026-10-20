"""Notification settings, channels (Web Push, ntfy, Telegram, ...), test sends, history and watchers.

Channel secrets never leave the server in full: configs are returned masked and
a masked value sent back unchanged keeps the stored secret.
"""

import re
from typing import Annotated, Literal

from fastapi import APIRouter, HTTPException, Query, Request, Response
from pydantic import BaseModel, Field, StringConstraints, field_validator
from sqlalchemy import delete, func, select

from ..models import AccountSettings, NotificationChannel, NotificationLog
from ..services import notify, notify_channels, webpush
from .deps import DbDep, SettingsDep, get_or_404

router = APIRouter(prefix="/api/notifications", tags=["notifications"])

UserEvent = Literal["strategy_alert", "risk", "news"]
Name = Annotated[str, StringConstraints(max_length=80, strip_whitespace=True)]
APP_URL_RE = re.compile(r"^https?://[^\s/?#@]+(/[^\s?#]*)?$")
CONTACT_RE = re.compile(r"^(mailto:[^\s@/]+@[^\s@/]+\.[^\s@/]+|https://[^\s/]+\S*)$")
WEBPUSH_FROM_DEVICE = (
    "Web Push is turned on from the device itself: open this page there and choose “Enable on this device”."
)


class SettingsIn(BaseModel):
    enabled: bool | None = None
    events: list[UserEvent] | None = None
    news_reminder_min: int | None = Field(None, ge=0, le=120)
    app_url: Annotated[str, StringConstraints(max_length=200, strip_whitespace=True)] | None = None
    vapid_contact: Annotated[str, StringConstraints(max_length=120, strip_whitespace=True)] | None = None

    @field_validator("app_url")
    @classmethod
    def _app_url(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.rstrip("/")
        if value and not APP_URL_RE.match(value):
            raise ValueError("The app link must be an http:// or https:// address, e.g. http://192.168.1.20:8765")
        return value

    @field_validator("vapid_contact")
    @classmethod
    def _contact(cls, value: str | None) -> str | None:
        if value is not None and not CONTACT_RE.match(value):
            raise ValueError("The contact must be mailto:you@example.com or an https:// address")
        return value


class ChannelIn(BaseModel):
    kind: Annotated[str, StringConstraints(max_length=12)]
    name: Name = ""
    enabled: bool = True
    events: list[UserEvent] = []
    config: dict = {}


class ChannelUpdate(BaseModel):
    name: Name | None = None
    enabled: bool | None = None
    events: list[UserEvent] | None = None
    config: dict | None = None


class PushKeys(BaseModel):
    p256dh: Annotated[str, StringConstraints(max_length=200)]
    auth: Annotated[str, StringConstraints(max_length=100)]


class PushSubscription(BaseModel):
    endpoint: Annotated[str, StringConstraints(max_length=1000)]
    keys: PushKeys


class SubscribeIn(BaseModel):
    subscription: PushSubscription
    name: Name = ""
    old_endpoint: Annotated[str, StringConstraints(max_length=1000)] = ""  # set when the browser rotated it


class UnsubscribeIn(BaseModel):
    endpoint: Annotated[str, StringConstraints(max_length=1000)]


# --------------------------------------------------------------- serializers


def _settings_out(db, settings: AccountSettings) -> dict:
    return {
        "enabled": settings.notifications_enabled,
        "events": [event for event in notify.USER_EVENTS if event in (settings.notify_events or [])],
        "news_reminder_min": settings.news_reminder_min,
        "app_url": settings.app_url,
        "vapid_contact": settings.vapid_contact,
        "vapid_public_key": settings.vapid_public_key,
        "webpush_available": webpush.AVAILABLE,
        "channels_count": db.scalar(select(func.count(NotificationChannel.id))) or 0,
        "events_available": [
            {"id": event, "label": notify.EVENT_LABELS[event], "description": notify.EVENT_DESCRIPTIONS[event]}
            for event in notify.USER_EVENTS
        ],
    }


def _channel_out(channel: NotificationChannel) -> dict:
    config = channel.config or {}
    out = {
        "id": channel.id,
        "kind": channel.kind,
        "kind_label": (
            notify_channels.KINDS[channel.kind].label if channel.kind in notify_channels.KINDS else channel.kind
        ),
        "name": channel.name,
        "enabled": channel.enabled,
        "events": channel.events or [],
        "config": notify_channels.masked_config(channel.kind, config),
        "summary": notify_channels.summary(channel.kind, config),
        "last_sent_at": channel.last_sent_at,
        "last_error": channel.last_error,
        "created_at": channel.created_at,
    }
    if channel.kind == "webpush":
        out["endpoint_id"] = notify_channels.endpoint_id(config.get("endpoint", ""))
    return out


def _log_out(row: NotificationLog) -> dict:
    return {
        "id": row.id,
        "created_at": row.created_at,
        "event": row.event,
        "event_label": notify.EVENT_LABELS.get(row.event, row.event),
        "title": row.title,
        "body": row.body,
        "url": row.url,
        "status": row.status,
        "results": row.results or [],
        "data": row.data or {},
    }


def _ordered_events(events: list[str]) -> list[str]:
    return [event for event in notify.USER_EVENTS if event in events]


def _config_error(exc: notify_channels.ChannelConfigError) -> HTTPException:
    return HTTPException(status_code=422, detail=str(exc))


# ----------------------------------------------------------------- settings


@router.get("/settings")
def get_settings(db: DbDep, settings: SettingsDep) -> dict:
    return _settings_out(db, settings)


@router.put("/settings")
def update_settings(body: SettingsIn, db: DbDep, settings: SettingsDep) -> dict:
    if body.enabled is not None:
        settings.notifications_enabled = body.enabled
    if body.events is not None:
        settings.notify_events = _ordered_events(body.events)
    if body.news_reminder_min is not None:
        settings.news_reminder_min = body.news_reminder_min
    if body.app_url is not None:
        settings.app_url = body.app_url
    if body.vapid_contact is not None:
        settings.vapid_contact = body.vapid_contact
    db.commit()
    return _settings_out(db, settings)


# ----------------------------------------------------------------- channels


@router.get("/channels")
def list_channels(db: DbDep) -> list[dict]:
    channels = db.scalars(select(NotificationChannel).order_by(NotificationChannel.id)).all()
    return [_channel_out(channel) for channel in channels]


@router.post("/channels", status_code=201)
def create_channel(body: ChannelIn, db: DbDep) -> dict:
    if body.kind == "webpush":
        raise HTTPException(status_code=422, detail=WEBPUSH_FROM_DEVICE)
    try:
        config = notify_channels.validate(body.kind, body.config)
    except notify_channels.ChannelConfigError as exc:
        raise _config_error(exc) from exc
    channel = NotificationChannel(
        kind=body.kind,
        name=body.name or notify_channels.KINDS[body.kind].label,
        enabled=body.enabled,
        events=_ordered_events(body.events),
        config=config,
    )
    db.add(channel)
    db.commit()
    return _channel_out(channel)


@router.put("/channels/{channel_id}")
def update_channel(channel_id: int, body: ChannelUpdate, db: DbDep) -> dict:
    channel = get_or_404(db, NotificationChannel, channel_id)
    if body.config is not None:
        try:
            channel.config = notify_channels.merge_config(channel.kind, channel.config or {}, body.config)
        except notify_channels.ChannelConfigError as exc:
            raise _config_error(exc) from exc
    if body.name is not None:
        channel.name = body.name or notify_channels.KINDS[channel.kind].label
    if body.enabled is not None:
        channel.enabled = body.enabled
        if body.enabled:
            channel.last_error = ""
    if body.events is not None:
        channel.events = _ordered_events(body.events)
    db.commit()
    return _channel_out(channel)


@router.delete("/channels/{channel_id}", status_code=204)
def delete_channel(channel_id: int, db: DbDep) -> Response:
    db.delete(get_or_404(db, NotificationChannel, channel_id))
    db.commit()
    return Response(status_code=204)


@router.post("/channels/{channel_id}/test")
def test_channel(channel_id: int, db: DbDep, settings: SettingsDep) -> dict:
    channel = get_or_404(db, NotificationChannel, channel_id)
    return _log_out(notify.send_to_channel(db, settings, channel, notify.test_note()))


@router.post("/test")
def test_all(db: DbDep, settings: SettingsDep) -> dict:
    return _log_out(notify.send(db, settings, notify.test_note()))


# ----------------------------------------------------------------- Web Push


@router.get("/webpush/key")
def webpush_key(db: DbDep, settings: SettingsDep) -> dict:
    if not webpush.AVAILABLE:
        return {"public_key": "", "available": False}
    return {"public_key": notify.ensure_vapid_keys(db, settings), "available": True}


@router.post("/webpush/subscribe")
def webpush_subscribe(body: SubscribeIn, request: Request, db: DbDep) -> dict:
    name = body.name or notify.device_label(request.headers.get("user-agent", ""))
    try:
        channel = notify.upsert_webpush(db, body.subscription.model_dump(), name, body.old_endpoint)
    except notify_channels.ChannelConfigError as exc:
        raise _config_error(exc) from exc
    return _channel_out(channel)


@router.post("/webpush/unsubscribe")
def webpush_unsubscribe(body: UnsubscribeIn, db: DbDep) -> dict:
    return {"removed": notify.remove_webpush(db, body.endpoint)}


# ------------------------------------------------------------ history, jobs


@router.get("/log")
def list_log(db: DbDep, limit: int = Query(50, ge=1, le=200)) -> list[dict]:
    rows = db.scalars(select(NotificationLog).order_by(NotificationLog.id.desc()).limit(limit)).all()
    return [_log_out(row) for row in rows]


@router.delete("/log", status_code=204)
def clear_log(db: DbDep) -> Response:
    db.execute(delete(NotificationLog))
    db.commit()
    return Response(status_code=204)


@router.get("/jobs")
def jobs(request: Request) -> list[dict]:
    return [job for job in request.app.state.scheduler.status() if job["name"] in notify.JOB_NAMES]
