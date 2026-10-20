"""Notifications: deliver a short message to the user's phone / browser (track N replaces this stub).

Contract used by the rest of the app (keep these signatures):

* ``Note`` - what to send. ``event`` is one of ``EVENTS``; ``title`` and ``body``
  are plain text (no markup); ``url`` is an in-app path such as
  ``/#/alerts?id=12`` (made absolute with ``AccountSettings.app_url`` when a
  channel needs a full link); ``priority`` is low | default | high.
* ``send(db, settings, note)`` delivers ``note`` synchronously to every enabled
  channel subscribed to its event, records a ``NotificationLog`` row and returns
  it. It returns None without sending when notifications are off or the event
  type is disabled - except ``test``, which is always sent. It never raises
  because a channel failed; failures are recorded per channel in the log row.
* ``register_jobs(scheduler, config)`` registers background watchers (risk
  events, news reminders) on the app's ``services.scheduler.Scheduler``.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from ..models import AccountSettings, NotificationLog

EVENTS = ("strategy_alert", "risk", "news", "test")


@dataclass
class Note:
    event: str
    title: str
    body: str
    url: str = ""
    priority: str = "default"  # low | default | high
    data: dict = field(default_factory=dict)
    tags: list[str] = field(default_factory=list)


def send(db: Session, settings: AccountSettings, note: Note) -> NotificationLog | None:
    """Deliver ``note`` (stub: notifications not implemented yet)."""
    return None


def register_jobs(scheduler, config) -> None:
    """Register background notification watchers (stub: none)."""
