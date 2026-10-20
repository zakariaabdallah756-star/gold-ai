"""Recommended partners, referral links and the risk disclaimer."""

from typing import Annotated, Literal

from fastapi import APIRouter, HTTPException, Response
from pydantic import BaseModel, Field, StringConstraints, field_validator
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from ..models import Partner
from ..services import disclaimer
from ..services.account import local_now
from ..services.partners import partner_link
from .deps import DbDep, SettingsDep, get_or_404

router = APIRouter(prefix="/api", tags=["partners"])

Url = Annotated[str, StringConstraints(max_length=500, strip_whitespace=True)]


def _http_url(value: str) -> str:
    if value and not value.lower().startswith(("http://", "https://")):
        raise ValueError("Links must start with http:// or https://")
    return value


class PartnerIn(BaseModel):
    name: Annotated[str, StringConstraints(min_length=1, max_length=80, strip_whitespace=True)]
    kind: Literal["broker", "prop_firm"] = "broker"
    website_url: Url = ""
    referral_url: Url = ""
    description: Annotated[str, StringConstraints(max_length=2000)] = ""
    highlights: list[Annotated[str, StringConstraints(max_length=120, strip_whitespace=True)]] = Field(
        default_factory=list, max_length=8
    )
    featured: bool = False
    sort_order: int = 0
    active: bool = True

    @field_validator("website_url", "referral_url")
    @classmethod
    def _http_only(cls, value: str) -> str:
        return _http_url(value)

    @field_validator("highlights")
    @classmethod
    def _drop_blank(cls, values: list[str]) -> list[str]:
        return [value for value in values if value]


class PartnerOut(PartnerIn):
    id: int


class ReferralIn(BaseModel):
    referral_url: Url = ""

    @field_validator("referral_url")
    @classmethod
    def _http_only(cls, value: str) -> str:
        return _http_url(value)


def _commit(db) -> None:
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="A partner with that name already exists") from exc


def _ordered():
    return select(Partner).order_by(Partner.featured.desc(), Partner.sort_order, Partner.name)


@router.get("/partners")
def list_partners(db: DbDep):
    """Active partners for the Recommended Partners page (featured first)."""
    return [
        {
            "id": p.id,
            "name": p.name,
            "kind": p.kind,
            "description": p.description,
            "highlights": p.highlights,
            "featured": p.featured,
            "website_url": p.website_url,
            "link": partner_link(p),
            "is_referral": bool(p.referral_url),
        }
        for p in db.scalars(_ordered().where(Partner.active.is_(True)))
    ]


@router.get("/partners/all", response_model=list[PartnerOut])
def list_all_partners(db: DbDep):
    """Every partner including inactive ones and referral links (settings)."""
    return db.scalars(_ordered()).all()


@router.post("/partners", response_model=PartnerOut, status_code=201)
def create_partner(payload: PartnerIn, db: DbDep):
    partner = Partner(**payload.model_dump())
    db.add(partner)
    _commit(db)
    return partner


@router.put("/partners/{partner_id}", response_model=PartnerOut)
def update_partner(partner_id: int, payload: PartnerIn, db: DbDep):
    partner = get_or_404(db, Partner, partner_id)
    for key, value in payload.model_dump().items():
        setattr(partner, key, value)
    _commit(db)
    return partner


@router.put("/partners/{partner_id}/referral", response_model=PartnerOut)
def set_referral(partner_id: int, payload: ReferralIn, db: DbDep):
    partner = get_or_404(db, Partner, partner_id)
    partner.referral_url = payload.referral_url
    db.commit()
    return partner


@router.delete("/partners/{partner_id}", status_code=204)
def delete_partner(partner_id: int, db: DbDep):
    db.delete(get_or_404(db, Partner, partner_id))
    db.commit()
    return Response(status_code=204)


@router.get("/disclaimer")
def read_disclaimer(settings: SettingsDep):
    return disclaimer.payload(settings)


@router.post("/disclaimer/accept")
def accept_disclaimer(settings: SettingsDep, db: DbDep):
    settings.disclaimer_accepted_at = local_now(settings)
    settings.disclaimer_version = disclaimer.DISCLAIMER_VERSION
    db.commit()
    return disclaimer.payload(settings)
