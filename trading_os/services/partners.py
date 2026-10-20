"""Recommended partners (brokers and prop firms).

``seed_partners(session)`` is called on every startup by ``seed.seed_defaults``
and must only insert the curated defaults when the table is empty (contract).
"""

from __future__ import annotations

from sqlalchemy.orm import Session


def seed_partners(session: Session) -> None:
    """Insert the curated default partners on first run (stub: nothing yet)."""
