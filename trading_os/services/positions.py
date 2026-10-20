"""Position management: scale-out targets, break-even, trailing stop, fill ledger.

``recompute`` is called by ``services.trades`` after every create/update and
must keep the derived position fields consistent (contract):

* ``current_stop``   - active stop: last stop-move event's ``stop_after``, else ``stop_loss``
* ``remaining_size`` - lots still open
* ``realized_pnl``   - sum of fill-event P&L (before fees)
* ``remaining_risk`` - money lost if the remainder is stopped at ``current_stop`` (0 when risk-free/closed)
* ``managed``        - True when the trade has fill events (then status/exit/P&L derive from them)
"""

from __future__ import annotations

from ..models import AccountSettings, Market, Trade


def recompute(trade: Trade, market: Market | None, settings: AccountSettings) -> None:
    """Refresh derived position fields (stub: no fill ledger yet)."""
    stops = [e for e in trade.events if e.stop_after is not None]
    trade.current_stop = stops[-1].stop_after if stops else trade.stop_loss
    trade.managed = any(e.is_fill for e in trade.events)
    trade.remaining_size = 0.0 if trade.status == "closed" else trade.position_size
    trade.realized_pnl = round(sum(e.pnl or 0.0 for e in trade.events if e.is_fill), 2)
    trade.remaining_risk = 0.0 if trade.status == "closed" else trade.risk_amount
