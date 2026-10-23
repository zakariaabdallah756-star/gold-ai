"""Order bridges: how Trading OS reaches a trading account. ``services.execution`` sends through them.

Every bridge validates its ``connection_config``, sends an order (``submit`` -> sent | filled | pending |
rejected | error, a broker reference, the fill price and a message), may report the account figures
(``snapshot``) and has a connection ``test``:

* ``paper``        simulated fills, nothing leaves the app (safe default, used by the demo);
* ``webhook``      a signed JSON POST to the trader's own bridge;
* ``mt_bridge``    queued for the MetaTrader 4/5 Expert Advisor that polls the app (``trading_os/bridges/``);
* ``mt5_terminal`` the ``MetaTrader5`` Python package on Windows;
* ``oanda``        the OANDA v20 REST API.
"""

from __future__ import annotations

from .base import (
    Bridge,
    BridgeConfigError,
    BridgeContext,
    BridgeResult,
    BridgeUnavailable,
    Order,
    TestResult,
    sanitize,
    truncate,
)
from .mt5_terminal import Mt5TerminalBridge
from .mt_bridge import MtBridge
from .oanda import OandaBridge
from .paper import PaperBridge
from .webhook import WebhookBridge

BRIDGES: dict[str, Bridge] = {
    bridge.kind: bridge for bridge in (PaperBridge(), WebhookBridge(), MtBridge(), Mt5TerminalBridge(), OandaBridge())
}


def get(kind: str) -> Bridge | None:
    """The bridge of a connection type (None for ``none`` or an unknown type)."""
    return BRIDGES.get(kind)


__all__ = [
    "BRIDGES",
    "Bridge",
    "BridgeConfigError",
    "BridgeContext",
    "BridgeResult",
    "BridgeUnavailable",
    "Order",
    "TestResult",
    "get",
    "sanitize",
    "truncate",
]
