"""Dashboard promo carousel: platform announcements, partner cards and the trader's own discount cards.

* **Seeding** - ``seed_promos`` runs on every start-up from ``seed.seed_defaults``. Each built-in announcement
  and the set of partner cards is its own ``seeded_defaults`` group, so a card the trader deleted never comes
  back, and an announcement added by a later version still appears once.
* **Partner cards** follow their partner: an empty title, text or link shows the partner's own name, description
  and link. Only *featured* partners get a card, and there are never discount codes or offers for real firms -
  discount cards are written by the trader, who types the code and offer text they were given.
* **Display** - the built-in texts follow the language until the trader edits them (like
  ``partners.display_text``). Partner and discount cards carry the neutral risk disclaimer of the Partners page.
* **Shown** = active, today (in the account time zone) inside ``starts_on``/``ends_on``, partner still active.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from datetime import date
from urllib.parse import urlsplit

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..models import AccountSettings, Partner, Promo
from .account import local_today
from .i18n import N_, tr
from .partners import display_text, partner_link

KINDS = ("announcement", "partner", "discount")
THEMES = ("teal", "blue", "gold", "purple", "red")
MIN_INTERVAL_SEC = 3
MAX_INTERVAL_SEC = 60
TITLE_MAX, BODY_MAX, CTA_LABEL_MAX, CTA_URL_MAX, CODE_MAX = 80, 300, 40, 500, 40

DISCLAIMER = N_(
    "Trading involves substantial risk of loss and is not suitable for everyone. Content and tools are for "
    "organizational and educational purposes only and do not constitute financial advice."
)
DEFAULT_CTA = N_("Learn more")
PARTNER_CTA = N_("Visit {name}")

# Built-in platform announcements, keyed by ``Promo.builtin_key``. The texts are English sources shown translated.
BUILTINS: dict[str, dict[str, str]] = {
    "whats_new_v15": {
        "title": N_("New in v1.5: multiple trading accounts"),
        "body": N_(
            "Track live, demo and prop-firm accounts side by side. Every trade is checked against its own "
            "account's equity, daily loss limit and trade count."
        ),
        "cta_label": N_("Set up your accounts"),
        "cta_url": "#/accounts",
        "theme": "purple",
    },
    "connect_account": {
        "title": N_("Connect your MT4, MT5 or broker account"),
        "body": N_(
            "Link a trading account to keep its balance, equity and open positions up to date. Credentials stay "
            "encrypted on this computer."
        ),
        "cta_label": N_("Connect an account"),
        "cta_url": "#/accounts",
        "theme": "teal",
    },
    "onboarding_guide": {
        "title": N_("New here? Watch the onboarding guide"),
        "body": N_(
            "A short walkthrough of the daily routine: pre-market checklist, risk calculator, journal and review."
        ),
        "cta_label": N_("Watch the guide"),
        "cta_url": "#/onboarding",
        "theme": "blue",
    },
    "strategy_alerts": {
        "title": N_("Set up your strategy alerts"),
        "body": N_(
            "Get notified when your setups form on the markets you trade. Alerts never place orders: every "
            "decision stays yours."
        ),
        "cta_label": N_("Set up alerts"),
        "cta_url": "#/alerts",
        "theme": "gold",
    },
}
BUILTIN_TEXT_FIELDS = ("title", "body", "cta_label")
PARTNER_GROUP = "promos:partners"
PARTNER_THEMES = {"prop_firm": "gold", "broker": "blue"}

_ROUTE = re.compile(r"^#/[A-Za-z0-9_\-./?=&%]*$")
_CONTROL = re.compile(r"[\x00-\x20\x7f]")


class PromoError(ValueError):
    """A promo that cannot be saved (the message is shown to the trader)."""


# ------------------------------------------------------------------ validation


def validate_cta_url(value: str) -> str:
    """An in-app route (``#/journal``) or an ``https://`` link; anything else (``javascript:``, ``http:``) is
    refused. Empty means no button (or the partner's own link)."""
    value = (value or "").strip()
    if not value:
        return ""
    if len(value) > CTA_URL_MAX:
        raise PromoError(tr("The link is too long (at most {limit} characters)", limit=CTA_URL_MAX))
    if value.startswith("#"):
        if not _ROUTE.match(value):
            raise PromoError(tr("App links look like #/journal (letters, digits and / ? = & - _ . only)"))
        return value
    if _CONTROL.search(value) or not value.lower().startswith("https://"):
        raise PromoError(tr("Links must be an app page such as #/journal or start with https://"))
    parts = urlsplit(value)
    if parts.scheme.lower() != "https" or not parts.hostname or "@" in parts.netloc:
        raise PromoError(tr("Links must be an app page such as #/journal or start with https://"))
    return value


def validate_code(value: str) -> str:
    value = (value or "").strip()
    if _CONTROL.search(value):
        raise PromoError(tr("A discount code cannot contain spaces"))
    return value


def check_dates(starts_on: date | None, ends_on: date | None) -> None:
    if starts_on and ends_on and ends_on < starts_on:
        raise PromoError(tr("The end date must be on or after the start date"))


def is_https(url: str) -> bool:
    return (url or "").lower().startswith("https://")


def prepare(db: Session, values: dict, promo: Promo | None = None) -> dict:
    """Check a create/update payload against the database and normalise it. Raises ``PromoError``."""
    values = dict(values)
    kind = values["kind"]
    partner_id = values.get("partner_id")
    if kind == "announcement":
        partner_id = None
    elif partner_id is not None and db.get(Partner, partner_id) is None:
        raise PromoError(tr("Unknown partner {partner_id}", partner_id=partner_id))
    if kind == "partner" and partner_id is None:
        raise PromoError(tr("Choose the partner this card is about"))
    values["partner_id"] = partner_id
    if not values.get("title") and partner_id is None:
        raise PromoError(tr("A title is required"))
    if kind != "discount":
        values["code"] = ""
    check_dates(values.get("starts_on"), values.get("ends_on"))
    builtin = BUILTINS.get(promo.builtin_key) if promo is not None and promo.builtin_key else None
    if builtin:
        # A built-in text left as shown (in the current language) keeps its English source, so it still follows
        # the language; edited text is kept as typed.
        for name in BUILTIN_TEXT_FIELDS:
            if values.get(name) == tr(builtin[name]):
                values[name] = builtin[name]
    return values


# --------------------------------------------------------------------- seeding


def _next_order(db: Session) -> int:
    highest = db.scalar(select(func.max(Promo.sort_order)))
    return 0 if highest is None else highest + 1


def seed_promos(session: Session, done: set[str]) -> None:
    """Insert the built-in announcements and one card per featured partner, each group at most once.

    ``done`` is the set of ``seeded_defaults`` groups (``seed.seed_defaults`` saves it): a group already in it
    is skipped, so cards the trader deleted never come back.
    """
    session.flush()  # partners seeded in the same transaction need their ids
    order = _next_order(session)
    for key, values in BUILTINS.items():
        group = f"promos:{key}"
        if group in done:
            continue
        done.add(group)
        if session.scalar(select(Promo.id).where(Promo.builtin_key == key).limit(1)) is not None:
            continue
        session.add(Promo(kind="announcement", builtin_key=key, sort_order=order, **values))
        order += 1
    if PARTNER_GROUP not in done:
        done.add(PARTNER_GROUP)
        if session.scalar(select(Promo.id).where(Promo.kind == "partner").limit(1)) is None:
            featured = session.scalars(
                select(Partner)
                .where(Partner.featured.is_(True), Partner.active.is_(True))
                .order_by(Partner.sort_order, Partner.name)
            )
            for partner in featured:
                # Empty texts and link: the card shows the partner's own name, description and link.
                session.add(
                    Promo(
                        kind="partner",
                        title="",
                        partner_id=partner.id,
                        theme=PARTNER_THEMES.get(partner.kind, "blue"),
                        sort_order=order,
                    )
                )
                order += 1
    session.flush()


# --------------------------------------------------------------------- display


def ordered(db: Session) -> list[Promo]:
    return list(db.scalars(select(Promo).order_by(Promo.sort_order, Promo.id)))


def _partners(db: Session, promos: Iterable[Promo]) -> dict[int, Partner]:
    ids = {p.partner_id for p in promos if p.partner_id is not None}
    if not ids:
        return {}
    return {partner.id: partner for partner in db.scalars(select(Partner).where(Partner.id.in_(ids)))}


def status(promo: Promo, partner: Partner | None, today: date) -> str:
    """live | off | scheduled | ended | partner_hidden | no_partner."""
    if not promo.active:
        return "off"
    if promo.kind == "partner" and partner is None:
        return "no_partner"
    if partner is not None and not partner.active:
        return "partner_hidden"
    if promo.starts_on and promo.starts_on > today:
        return "scheduled"
    if promo.ends_on and promo.ends_on < today:
        return "ended"
    return "live"


def editable_text(promo: Promo, name: str) -> str:
    """A text field as the editor shows it: built-in texts translated until edited, the rest as typed."""
    value = getattr(promo, name) or ""
    builtin = BUILTINS.get(promo.builtin_key or "")
    if builtin and value and value == builtin.get(name):
        return tr(value)
    return value


def _shorten(text: str, limit: int = BODY_MAX) -> str:
    text = " ".join((text or "").split())
    if len(text) <= limit:
        return text
    cut = text[: limit - 1].rsplit(" ", 1)[0].rstrip(",;:.-")
    return f"{cut}…"


def partner_info(partner: Partner) -> dict:
    return {
        "id": partner.id,
        "name": partner.name,
        "kind": partner.kind,
        "active": partner.active,
        "featured": partner.featured,
        "description": _shorten(display_text(partner.description)),
        "link": partner_link(partner),
    }


def card(promo: Promo, partner: Partner | None) -> dict:
    """The card as the carousel shows it: translated, with the partner's texts and link filling empty fields."""
    title = editable_text(promo, "title") or (partner.name if partner else "")
    body = editable_text(promo, "body")
    if not body and partner is not None and promo.kind == "partner":
        body = _shorten(display_text(partner.description))  # a discount card's text is the trader's own
    url = promo.cta_url or ""
    if not url and partner is not None:
        link = partner_link(partner)
        url = link if is_https(link) else "#/partners"  # carousel links are app pages or https:// only
    label = editable_text(promo, "cta_label")
    if url and not label:
        label = tr(PARTNER_CTA, name=partner.name) if partner is not None and not promo.cta_url else tr(DEFAULT_CTA)
    with_disclaimer = promo.kind in ("partner", "discount")
    return {
        "id": promo.id,
        "kind": promo.kind,
        "theme": promo.theme if promo.theme in THEMES else "teal",
        "builtin_key": promo.builtin_key or "",
        "title": title,
        "body": body,
        "cta_label": label if url else "",
        "cta_url": url,
        "external": is_https(url),
        "code": promo.code if promo.kind == "discount" else "",
        "partner": {"id": partner.id, "name": partner.name, "kind": partner.kind} if partner else None,
        "disclaimer": with_disclaimer,
        "disclaimer_text": tr(DISCLAIMER) if with_disclaimer else "",
    }


def carousel(db: Session, settings: AccountSettings) -> dict:
    """``GET /api/promos``: the promos shown today, in order (none while the carousel is switched off)."""
    payload = {"enabled": bool(settings.promos_enabled), "interval_sec": interval(settings), "promos": []}
    if not settings.promos_enabled:
        return payload
    today = local_today(settings)
    promos = ordered(db)
    partners = _partners(db, promos)
    for promo in promos:
        partner = partners.get(promo.partner_id) if promo.partner_id is not None else None
        if status(promo, partner, today) == "live":
            payload["promos"].append(card(promo, partner))
    return payload


def editor_item(promo: Promo, partner: Partner | None, today: date) -> dict:
    return {
        "id": promo.id,
        "kind": promo.kind,
        "title": editable_text(promo, "title"),
        "body": editable_text(promo, "body"),
        "cta_label": editable_text(promo, "cta_label"),
        "cta_url": promo.cta_url or "",
        "code": promo.code or "",
        "partner_id": promo.partner_id,
        "theme": promo.theme,
        "starts_on": promo.starts_on,
        "ends_on": promo.ends_on,
        "active": promo.active,
        "sort_order": promo.sort_order,
        "builtin_key": promo.builtin_key or "",
        "builtin": bool(promo.builtin_key),
        "status": status(promo, partner, today),
        "display": card(promo, partner),
    }


def editor(db: Session, settings: AccountSettings) -> dict:
    """``GET /api/promos/all``: every promo with its status, plus the partners a card can be about."""
    today = local_today(settings)
    promos = ordered(db)
    all_partners = list(db.scalars(select(Partner).order_by(Partner.featured.desc(), Partner.sort_order)))
    by_id = {partner.id: partner for partner in all_partners}
    return {
        "enabled": bool(settings.promos_enabled),
        "interval_sec": interval(settings),
        "today": today,
        "promos": [editor_item(p, by_id.get(p.partner_id), today) for p in promos],
        "partners": [partner_info(partner) for partner in all_partners],
        "disclaimer_text": tr(DISCLAIMER),
    }


def item(db: Session, settings: AccountSettings, promo: Promo) -> dict:
    partner = db.get(Partner, promo.partner_id) if promo.partner_id is not None else None
    return editor_item(promo, partner, local_today(settings))


def interval(settings: AccountSettings) -> int:
    value = settings.promo_interval_sec or 7
    return max(MIN_INTERVAL_SEC, min(MAX_INTERVAL_SEC, int(value)))


# ----------------------------------------------------------------- write side


def create(db: Session, values: dict) -> Promo:
    promo = Promo(**prepare(db, values), sort_order=_next_order(db))
    db.add(promo)
    db.flush()
    return promo


def update(db: Session, promo: Promo, values: dict) -> Promo:
    for key, value in prepare(db, values, promo).items():
        setattr(promo, key, value)
    db.flush()
    return promo


def reorder(db: Session, ids: list[int]) -> list[Promo]:
    """Put ``ids`` first, in that order; promos not listed keep their relative order after them."""
    if len(set(ids)) != len(ids):
        raise PromoError(tr("Each promotion can appear only once in the order"))
    promos = ordered(db)
    by_id = {promo.id: promo for promo in promos}
    unknown = [promo_id for promo_id in ids if promo_id not in by_id]
    if unknown:
        raise PromoError(tr("Unknown promotion {promo_id}", promo_id=unknown[0]))
    listed = set(ids)
    sequence = [by_id[promo_id] for promo_id in ids] + [p for p in promos if p.id not in listed]
    for index, promo in enumerate(sequence):
        promo.sort_order = index
    db.flush()
    return sequence
