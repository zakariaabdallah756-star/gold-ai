from backtest.backtest_position import BacktestPosition


class BacktestPositionManager:

    def __init__(self):
        self.positions: list[BacktestPosition] = []

    def open_position(self, position: BacktestPosition):
        self.positions.append(position)

    def get_open_positions(self):
        return [p for p in self.positions if p.is_open]

    def close_position(self, position: BacktestPosition):
        position.is_open = False

    def close_all(self):
        for position in self.positions:
            position.is_open = False