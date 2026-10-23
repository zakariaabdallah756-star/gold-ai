"""Dashboard promo carousel: the cards shown, the editor (Trading plan -> Promotions) and its settings."""

from datetime import date
from typing import Annotated, Literal

from fastapi import APIRouter, HTTPException, Response
from pydantic import Field, StringConstraints, field_validator, model_validator

from ..models import Promo
from ..schemas import FiniteModel
from ..services import promos
from .deps import DbDep, SettingsDep, get_or_404

router = APIRouter(prefix="/api/promos", tags=["promos"])


Title = Annotated[str, StringConstraints(max_length=promos.TITLE_MAX, strip_whitespace=True)]
Body = Annotated[str, StringConstraints(max_length=promos.BODY_MAX, strip_whitespace=True)]
CtaLabel = Annotated[str, StringConstraints(max_length=promos.CTA_LABEL_MAX, strip_whitespace=True)]
CtaUrl = Annotated[str, StringConstraints(max_length=promos.CTA_URL_MAX, strip_whitespace=True)]
Code = Annotated[str, StringConstraints(max_length=promos.CODE_MAX, strip_whitespace=True)]


class PromoIn(FiniteModel):
    kind: Literal["announcement", "partner", "discount"] = "announcement"
    title: Title = ""
    body: Body = ""
    cta_label: CtaLabel = ""
    cta_url: CtaUrl = ""
    code: Code = ""
    partner_id: int | None = None
    theme: Literal["teal", "blue", "gold", "purple", "red"] = "teal"
    starts_on: date | None = None
    ends_on: date | None = None
    active: bool = True

    @field_validator("cta_url")
    @classmethod
    def _cta_url(cls, value: str) -> str:
        return promos.validate_cta_url(value)

    @field_validator("code")
    @classmethod
    def _code(cls, value: str) -> str:
        return promos.validate_code(value)

    @model_validator(mode="after")
    def _dates(self):
        promos.check_dates(self.starts_on, self.ends_on)
        return self


class OrderIn(FiniteModel):
    ids: list[int] = Field(max_length=500)


class PromoSettingsIn(FiniteModel):
    enabled: bool | None = None
    interval_sec: int | None = Field(None, ge=promos.MIN_INTERVAL_SEC, le=promos.MAX_INTERVAL_SEC)


def _unprocessable(exc: promos.PromoError) -> HTTPException:
    return HTTPException(status_code=422, detail=str(exc))


@router.get("")
def carousel(db: DbDep, settings: SettingsDep):
    """Active promos for the dashboard carousel: translated, in order, with partner info and the disclaimer."""
    return promos.carousel(db, settings)


@router.get("/all")
def editor(db: DbDep, settings: SettingsDep):
    """Every promo with its status, the partners a card can be about and the carousel settings."""
    return promos.editor(db, settings)


@router.put("/settings")
def update_settings(payload: PromoSettingsIn, db: DbDep, settings: SettingsDep):
    """Switch the carousel on/off and set its rotation interval (also editable through PUT /api/settings)."""
    if payload.enabled is not None:
        settings.promos_enabled = payload.enabled
    if payload.interval_sec is not None:
        settings.promo_interval_sec = payload.interval_sec
    db.commit()
    return {"enabled": bool(settings.promos_enabled), "interval_sec": promos.interval(settings)}


@router.put("/order")
def reorder(payload: OrderIn, db: DbDep, settings: SettingsDep):
    try:
        promos.reorder(db, payload.ids)
    except promos.PromoError as exc:
        db.rollback()
        raise _unprocessable(exc) from exc
    db.commit()
    return promos.editor(db, settings)


@router.post("", status_code=201)
def create_promo(payload: PromoIn, db: DbDep, settings: SettingsDep):
    try:
        promo = promos.create(db, payload.model_dump())
    except promos.PromoError as exc:
        db.rollback()
        raise _unprocessable(exc) from exc
    db.commit()
    return promos.item(db, settings, promo)


@router.put("/{promo_id}")
def update_promo(promo_id: int, payload: PromoIn, db: DbDep, settings: SettingsDep):
    promo = get_or_404(db, Promo, promo_id)
    try:
        promos.update(db, promo, payload.model_dump())
    except promos.PromoError as exc:
        db.rollback()
        raise _unprocessable(exc) from exc
    db.commit()
    return promos.item(db, settings, promo)


@router.delete("/{promo_id}", status_code=204)
def delete_promo(promo_id: int, db: DbDep):
    db.delete(get_or_404(db, Promo, promo_id))
    db.commit()
    return Response(status_code=204)
