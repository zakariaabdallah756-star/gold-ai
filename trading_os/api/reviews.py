"""Post-market daily review endpoints."""

from datetime import date
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query
from pydantic import Field, StringConstraints, field_validator

from ..constants import MOODS
from ..schemas import FiniteModel
from ..services import reviews
from ..services.account import local_today
from .deps import DbDep, SettingsDep

router = APIRouter(prefix="/api", tags=["reviews"])

ReviewText = Annotated[str, StringConstraints(max_length=10_000)]
MistakeName = Annotated[str, StringConstraints(max_length=60, strip_whitespace=True)]


class ReviewIn(FiniteModel):
    discipline_rating: int | None = Field(None, ge=1, le=5)
    mood: str = ""
    followed_plan: bool | None = None
    went_well: ReviewText = ""
    went_wrong: ReviewText = ""
    lessons: ReviewText = ""
    tomorrow_focus: ReviewText = ""
    mistakes: list[MistakeName] = Field(default_factory=list, max_length=30)

    @field_validator("mood")
    @classmethod
    def _known_mood(cls, value: str) -> str:
        value = value.strip()
        if value and value not in MOODS:
            raise ValueError(f"Mood must be one of: {', '.join(MOODS)}")
        return value

    @field_validator("mistakes")
    @classmethod
    def _dedupe(cls, values: list[str]) -> list[str]:
        return list(dict.fromkeys(value for value in values if value))


class NameCount(FiniteModel):
    name: str
    count: int


class ReviewStats(FiniteModel):
    trades: int  # closed that day
    net_pnl: float  # booked that day, partial exits included
    partials: int = 0  # fills that did not close their trade
    wins: int
    losses: int
    win_rate: float | None
    compliance_rate: float | None
    mistakes: list[NameCount]
    emotions: list[NameCount]
    opened: int


class ReviewOut(ReviewIn):
    day: date
    exists: bool
    stats: ReviewStats


class ReviewHistoryRow(FiniteModel):
    day: date
    exists: bool
    discipline_rating: int | None
    mood: str
    followed_plan: bool | None
    pnl: float
    trades: int
    partials: int = 0


@router.get("/reviews", response_model=list[ReviewHistoryRow])
def read_history(settings: SettingsDep, db: DbDep, days: int = Query(30, ge=1, le=366)):
    """Every day of the last ``days`` days (today first), reviewed or not, with the P&L booked that day."""
    return reviews.history(db, local_today(settings), days)


@router.get("/reviews/{day}", response_model=ReviewOut)
def read_review(day: date, db: DbDep):
    return reviews.get_review(db, day)


@router.put("/reviews/{day}", response_model=ReviewOut)
def save_review(day: date, payload: ReviewIn, settings: SettingsDep, db: DbDep):
    """Create or replace the review of a day (today or earlier)."""
    if day > local_today(settings):
        raise HTTPException(status_code=422, detail="You can only review today or a past day.")
    return reviews.save_review(db, day, payload.model_dump())
