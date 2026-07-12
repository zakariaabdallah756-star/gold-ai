class BacktestPositionManager:

    def __init__(self):
        self.positions = []

    def open_position(self, position):
        self.positions.append(position)

    def get_open_positions(self):
        return self.positions

    def close_all(self):
        self.positions.clear()