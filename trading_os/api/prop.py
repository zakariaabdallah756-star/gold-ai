"""Prop-firm challenge tracker."""

from datetime import date
from typing import Annotated, Literal

from fastapi import APIRouter, HTTPException, Response
from pydantic import Field, StringConstraints
from sqlalchemy import select

from ..models import PropChallenge
from ..schemas import FiniteModel
from ..services import prop
from ..services.account import local_today
from ..services.i18n import tr
from .deps import DbDep, SettingsDep, get_or_404

router = APIRouter(prefix="/api/prop", tags=["prop"])

Percent = Annotated[float, Field(gt=0, le=100)]


class ChallengeIn(FiniteModel):
    name: Annotated[str, StringConstraints(min_length=1, max_length=80, strip_whitespace=True)]
    firm: Annotated[str, StringConstraints(max_length=60, strip_whitespace=True)] = ""
    preset: Literal[prop.PRESET_KEYS] = "custom"
    phase: Literal["phase1", "phase2", "funded", "custom"] = "phase1"
    account_size: float = Field(gt=0, le=100_000_000)
    profit_target_pct: Percent | None = None
    max_daily_loss_pct: Percent = 5.0
    max_total_drawdown_pct: Percent = 10.0
    drawdown_mode: Literal["static", "trailing"] = "static"
    min_trading_days: int = Field(0, ge=0, le=365)
    max_calendar_days: int | None = Field(None, ge=1, le=3650)
    start_date: date
    enforce: bool = True
    notes: Annotated[str, StringConstraints(max_length=5000)] = ""


class ChallengeCreate(ChallengeIn):
    activate: bool = True


class ChallengeUpdate(ChallengeIn):
    status: Literal["active", "passed", "failed", "archived"] | None = None


def _summary(challenge: PropChallenge, metrics: prop.Metrics) -> dict:
    return {
        "id": challenge.id,
        "name": challenge.name,
        "firm": challenge.firm,
        "preset": challenge.preset,
        "phase": challenge.phase,
        "account_size": challenge.account_size,
        "profit_target_pct": challenge.profit_target_pct,
        "max_daily_loss_pct": challenge.max_daily_loss_pct,
        "max_total_drawdown_pct": challenge.max_total_drawdown_pct,
        "drawdown_mode": challenge.drawdown_mode,
        "min_trading_days": challenge.min_trading_days,
        "max_calendar_days": challenge.max_calendar_days,
        "start_date": challenge.start_date,
        "end_date": challenge.end_date,
        "status": challenge.status,
        "status_reason": prop.status_reason(challenge, metrics),
        "active": challenge.active,
        "enforce": challenge.enforce,
        "notes": challenge.notes,
        "metrics": prop.metrics_dict(metrics),
    }


def _load(db, challenge_id: int) -> PropChallenge:
    return get_or_404(db, PropChallenge, challenge_id)


@router.get("/presets")
def list_presets():
    return {"presets": prop.presets(), "note": tr(prop.VERIFY_NOTE)}


@router.get("/challenges")
def list_challenges(settings: SettingsDep, db: DbDep):
    challenges = db.scalars(select(PropChallenge).order_by(PropChallenge.active.desc(), PropChallenge.id.desc()))
    return [_summary(c, prop.refresh(db, settings, c)) for c in challenges]


@router.post("/challenges", status_code=201)
def create_challenge(payload: ChallengeCreate, settings: SettingsDep, db: DbDep):
    data = payload.model_dump()
    activate = data.pop("activate")
    challenge = PropChallenge(**data, status="active", active=False)
    db.add(challenge)
    db.commit()
    if activate:
        prop.activate(db, challenge)
    return _summary(challenge, prop.refresh(db, settings, challenge))


@router.get("/challenges/{challenge_id}")
def get_challenge(challenge_id: int, settings: SettingsDep, db: DbDep):
    challenge = _load(db, challenge_id)
    return _summary(challenge, prop.refresh(db, settings, challenge))


@router.put("/challenges/{challenge_id}")
def update_challenge(challenge_id: int, payload: ChallengeUpdate, settings: SettingsDep, db: DbDep):
    """Edit the rules. Setting status back to "active" reopens an ended challenge (and re-evaluates it)."""
    challenge = _load(db, challenge_id)
    data = payload.model_dump()
    status = data.pop("status")
    for key, value in data.items():
        setattr(challenge, key, value)
    if status is not None and status != challenge.status:
        challenge.status = status
        challenge.end_date = None if status == "active" else challenge.end_date or local_today(settings)
        if status == "archived":
            challenge.active = False
    db.commit()
    return _summary(challenge, prop.refresh(db, settings, challenge))


@router.delete("/challenges/{challenge_id}", status_code=204)
def delete_challenge(challenge_id: int, db: DbDep):
    db.delete(_load(db, challenge_id))
    db.commit()
    return Response(status_code=204)


@router.post("/challenges/{challenge_id}/activate")
def activate_challenge(challenge_id: int, settings: SettingsDep, db: DbDep):
    challenge = _load(db, challenge_id)
    if challenge.status == "archived":
        raise HTTPException(409, tr("An archived challenge can't drive prop mode - reopen it first"))
    prop.activate(db, challenge)
    return _summary(challenge, prop.refresh(db, settings, challenge))


@router.post("/challenges/{challenge_id}/deactivate")
def deactivate_challenge(challenge_id: int, settings: SettingsDep, db: DbDep):
    challenge = _load(db, challenge_id)
    challenge.active = False
    db.commit()
    return _summary(challenge, prop.refresh(db, settings, challenge))


@router.post("/challenges/{challenge_id}/archive")
def archive_challenge(challenge_id: int, settings: SettingsDep, db: DbDep):
    challenge = _load(db, challenge_id)
    challenge.active = False
    challenge.status = "archived"
    challenge.end_date = challenge.end_date or local_today(settings)
    db.commit()
    return _summary(challenge, prop.refresh(db, settings, challenge))


@router.get("/challenges/{challenge_id}/report")
def challenge_report(challenge_id: int, settings: SettingsDep, db: DbDep):
    challenge = _load(db, challenge_id)
    metrics = prop.refresh(db, settings, challenge)
    return {
        **_summary(challenge, metrics),
        "currency": settings.currency,
        "history": metrics.history,
        "rules_note": tr(prop.VERIFY_NOTE),
    }
