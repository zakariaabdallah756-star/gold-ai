"""Daily pre-market checklist endpoints."""

from datetime import date

from fastapi import APIRouter, Query

from ..schemas import ChecklistDayIn, ChecklistDayOut
from ..services import checklist
from ..services.account import local_now, local_today
from .deps import DbDep, SettingsDep

router = APIRouter(prefix="/api/checklist", tags=["checklist"])


@router.get("/today", response_model=ChecklistDayOut)
def read_today(settings: SettingsDep, db: DbDep):
    return checklist.get_day(db, local_today(settings))


@router.get("/history")
def read_history(settings: SettingsDep, db: DbDep, days: int = Query(30, ge=1, le=366)):
    return checklist.history(db, local_today(settings), days)


@router.get("/{day}", response_model=ChecklistDayOut)
def read_day(day: date, db: DbDep):
    return checklist.get_day(db, day)


@router.put("/{day}", response_model=ChecklistDayOut)
def save_day(day: date, payload: ChecklistDayIn, settings: SettingsDep, db: DbDep):
    return checklist.save_day(db, day, payload, local_now(settings))
