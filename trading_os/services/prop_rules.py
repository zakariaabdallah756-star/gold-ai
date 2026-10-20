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

A challenge that is still marked active after it ended adds a
PROP_CHALLENGE_ENDED warning until it is deactivated. A failed one keeps
reporting the breach that failed it (daily loss today, balance at the floor);
the risk budget and target checks only run while the challenge is running.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy.orm import Session

from ..models import AccountSettings, PropChallenge
from . import prop
from .guardrails import Violation


def _money(value: float, settings: AccountSettings) -> str:
    return f"{value:,.2f} {settings.currency}"


def _breaches(
    settings: AccountSettings, challenge: PropChallenge, metrics: prop.Metrics, risk_amount: float | None
) -> list[Violation]:
    status = challenge.status if challenge.status != "active" else metrics.evaluation.status
    severity = "block" if challenge.enforce else "warn"
    violations: list[Violation] = []
    if status != "active":
        reason = prop.status_reason(challenge, metrics) or metrics.evaluation.reason
        violations.append(
            Violation(
                "PROP_CHALLENGE_ENDED",
                f"Prop challenge '{challenge.name}' has ended ({status}{': ' + reason if reason else ''}). "
                "Deactivate it or start the next phase in Prop Firm.",
                "warn",
            )
        )
    if status in ("passed", "archived"):
        return violations

    # Account conditions: they keep applying after a breach has failed the challenge.
    if metrics.daily_loss_used >= metrics.daily_limit > 0:
        violations.append(
            Violation(
                "PROP_DAILY_LOSS",
                f"Prop rule: today's loss {_money(metrics.daily_loss_used, settings)} has reached the challenge's "
                f"daily limit of {_money(metrics.daily_limit, settings)}.",
                severity,
            )
        )
    if metrics.balance <= metrics.drawdown_floor:
        violations.append(
            Violation(
                "PROP_MAX_DRAWDOWN",
                f"Prop rule: balance {_money(metrics.balance, settings)} is at or below the max drawdown floor "
                f"of {_money(metrics.drawdown_floor, settings)}.",
                severity,
            )
        )
    if status != "active":
        return violations

    room = min(metrics.daily_remaining, metrics.total_remaining)
    if not violations and risk_amount and risk_amount > room:
        limit = "daily loss" if metrics.daily_remaining <= metrics.total_remaining else "max drawdown"
        violations.append(
            Violation(
                "PROP_RISK_BUDGET",
                f"Prop rule: this trade risks {_money(risk_amount, settings)} but only "
                f"{_money(max(room, 0.0), settings)} of {limit} room is left (after open risk). "
                "Reduce the size or skip the trade.",
                severity,
            )
        )
    if metrics.target_reached:
        missing = max(0, challenge.min_trading_days - metrics.trading_days)
        violations.append(
            Violation(
                "PROP_TARGET_REACHED",
                "Prop challenge profit target reached"
                + (f" - {missing} more trading day(s) needed; keep risk minimal" if missing else "")
                + ". Consider stopping to lock in the pass.",
                "warn",
            )
        )
    return violations


def entry_violations(
    db: Session,
    settings: AccountSettings,
    *,
    risk_amount: float | None,
    entry_time: datetime,
    now: datetime,
) -> list[Violation]:
    """Prop-rule violations for a new trade on the active challenge's account."""
    challenge = prop.active_challenge(db)
    if challenge is None or entry_time.date() < challenge.start_date:
        return []
    metrics = prop.compute(db, settings, challenge, today=now.date())
    return _breaches(settings, challenge, metrics, risk_amount)


def status_extras(db: Session, settings: AccountSettings, now: datetime) -> dict:
    """``reasons`` for the status bar and the ``prop`` snapshot of the active challenge (or None)."""
    challenge = prop.active_challenge(db)
    if challenge is None:
        return {"reasons": [], "prop": None}
    metrics = prop.refresh(db, settings, challenge)
    return {"reasons": _breaches(settings, challenge, metrics, None), "prop": prop.snapshot(challenge, metrics)}
