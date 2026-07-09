from execution.order import Order
from risk.trade_plan import TradePlan


class OrderBuilder:

    def build(self, symbol: str, trade_plan: TradePlan) -> Order:
        return Order(
            symbol=symbol,
            signal=trade_plan.signal,
            lot_size=trade_plan.lot_size,
            stop_loss_pips=trade_plan.stop_loss_pips,
            take_profit_pips=trade_plan.take_profit_pips,
        )