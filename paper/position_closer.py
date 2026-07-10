from strategy.signal import SignalType
from paper.position import Position


class PositionCloser:

    def should_close(self, position: Position, current_price: float):

        if position.signal == SignalType.BUY:
            return (
                current_price <= position.stop_loss
                or current_price >= position.take_profit
            )

        if position.signal == SignalType.SELL:
            return (
                current_price >= position.stop_loss
                or current_price <= position.take_profit
            )

        return False