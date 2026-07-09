from paper.position import Position
from paper.position_manager import PositionManager


class PaperEngine:

    def __init__(self):
        self.manager = PositionManager()

    def execute_trade(self, trade_plan):
        position = Position(
            symbol="XAUUSD",
            signal=trade_plan.signal,
            lot_size=trade_plan.lot_size,
            entry_price=3300.0,
            stop_loss=3280.0,
            take_profit=3340.0,
        )

        self.manager.open_position(position)

        return position