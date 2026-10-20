"""Strategy alert engine: scan market data for user-configured strategy setups (track S replaces this stub).

Contract: ``register_jobs(scheduler, config)`` registers the background scanner
on the app's ``services.scheduler.Scheduler``. Alerts are informational only -
the engine never creates or executes trades.
"""

from __future__ import annotations


def register_jobs(scheduler, config) -> None:
    """Register the alert scanner (stub: none)."""
