"""Context guardrails: economic-calendar windows and the emergency kill switch.

Hook points used by the rest of the app (signatures are a contract):

* ``entry_violations`` is called by ``services.trades.evaluate`` for every
  *new* trade. Return ``Violation`` objects with the severity the account's
  settings prescribe; ``evaluate`` downgrades them to warnings for trades that
  are not live entries.
* ``status_extras`` is merged into ``/api/risk/status`` by
  ``services.account.daily_status``.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy.orm import Session

from ..models import AccountSettings
from .guardrails import Violation


def entry_violations(
    db: Session,
    settings: AccountSettings,
    *,
    symbol: str,
    entry_time: datetime,
    now: datetime,
) -> list[Violation]:
    """NEWS_WINDOW / KILL_SWITCH violations for a new trade (stub: none)."""
    return []


def status_extras(db: Session, settings: AccountSettings, now: datetime) -> dict:
    """Extra status fields: ``reasons`` (list[Violation]), ``cooloff_until``,
    ``cooloff_reason`` and ``upcoming_news`` (stub: nothing active)."""
    return {"reasons": [], "cooloff_until": None, "cooloff_reason": "", "upcoming_news": []}
