"""Recommended partners (brokers and prop firms).

``seed_partners(session)`` is called on every startup by ``seed.seed_defaults``
and only inserts the curated defaults when the table is empty.

The defaults deliberately contain only names, official websites and neutral
descriptions - no claims about spreads, regulation or payouts, which change and
differ by country. The list is edited in Trading plan -> Partners, where each
partner can also get a custom link that the Visit button opens instead of the
official website.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Partner

VERIFY_NOTE = "Check current terms, fees and availability in your country on the official website"

DEFAULT_PARTNERS = (
    {
        "name": "FTMO",
        "kind": "prop_firm",
        "website_url": "https://ftmo.com",
        "description": "Proprietary trading firm offering evaluation programmes for funded trading accounts.",
        "highlights": ["Evaluation-based funded accounts", "Daily and overall loss limits", VERIFY_NOTE],
        "featured": True,
    },
    {
        "name": "FundedNext",
        "kind": "prop_firm",
        "website_url": "https://fundednext.com",
        "description": "Proprietary trading firm offering evaluation programmes for funded trading accounts.",
        "highlights": ["Evaluation-based funded accounts", "Daily and overall loss limits", VERIFY_NOTE],
        "featured": False,
    },
    {
        "name": "The5ers",
        "kind": "prop_firm",
        "website_url": "https://the5ers.com",
        "description": "Proprietary trading firm offering funded-account programmes.",
        "highlights": ["Funded-account programmes", VERIFY_NOTE],
        "featured": False,
    },
    {
        "name": "IC Markets",
        "kind": "broker",
        "website_url": "https://www.icmarkets.com",
        "description": "Online forex and CFD broker.",
        "highlights": ["Forex and CFD trading", "MetaTrader platforms", VERIFY_NOTE],
        "featured": True,
    },
    {
        "name": "Pepperstone",
        "kind": "broker",
        "website_url": "https://pepperstone.com",
        "description": "Online forex and CFD broker.",
        "highlights": ["Forex and CFD trading", "MetaTrader platforms", VERIFY_NOTE],
        "featured": False,
    },
)


def seed_partners(session: Session) -> None:
    """Insert the curated default partners on first run."""
    if session.scalar(select(Partner.id).limit(1)) is not None:
        return
    for order, values in enumerate(DEFAULT_PARTNERS):
        session.add(Partner(sort_order=order, **values))


def partner_link(partner: Partner) -> str:
    """The URL the Visit button opens: the custom link when one is set, else the official website."""
    return partner.link_url or partner.website_url
