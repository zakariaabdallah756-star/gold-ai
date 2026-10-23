"""Paper trading: a simulated account. Nothing ever leaves Trading OS.

Market orders fill at the order's price; limit and stop orders are accepted and wait as pending (they can be
cancelled). It is the safe default for trying the whole flow - and the demo account uses it.
"""

from __future__ import annotations

from ..i18n import tr
from .base import Bridge, BridgeContext, BridgeResult, Order, TestResult


class PaperBridge(Bridge):
    kind = "paper"
    cancellable = True

    def submit(self, ctx: BridgeContext, order: Order) -> BridgeResult:
        ref = f"PAPER-{order.id}"
        if not order.price:
            return BridgeResult("rejected", message=tr("A paper order needs a price"))
        if order.order_type == "market":
            return BridgeResult(
                "filled",
                ref,
                order.price,
                tr("Simulated fill at {price:g} - nothing was sent to a broker", price=order.price),
                {"simulated": True},
            )
        return BridgeResult(
            "pending",
            ref,
            None,
            tr(
                "Simulated {order_type} order at {price:g} is waiting - nothing was sent to a broker",
                order_type=order.order_type,
                price=order.price,
            ),
            {"simulated": True},
        )

    def test(self, ctx: BridgeContext) -> TestResult:
        return TestResult(True, tr("Paper trading is ready: orders are simulated and never reach a broker."))
