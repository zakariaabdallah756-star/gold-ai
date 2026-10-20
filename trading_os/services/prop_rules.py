"""Prop-firm guardrails ("prop mode", active while a challenge is marked active).

Hook points used by the rest of the app (signatures are a contract):

* ``entry_violations`` is called by ``services.trades.evaluate`` for every new
  trade dated today. Return ``PROP_*`` violations (e.g. PROP_DAILY_LOSS,
  PROP_MAX_DRAWDOWN, PROP_RISK_BUDGET) with severity "block" when the active
  challenge enforces its rules, else "warn"; ``evaluate`` downgrades them to
  warnings for trades that are not live entries.
* ``status_extras`` is merged into ``/api/risk/status`` by
  ``services.account.daily_status``: ``reasons`` (list[Violation]) and ``prop``
  (a snapshot dict of the active challenge, or None).
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
    risk_amount: float | None,
    entry_time: datetime,
    now: datetime,
) -> list[Violation]:
    """Prop-rule violations for a new trade (stub: no active challenge)."""
    return []


def status_extras(db: Session, settings: AccountSettings, now: datetime) -> dict:
    """Extra status fields for the active challenge (stub: prop mode off)."""
    return {"reasons": [], "prop": None}
