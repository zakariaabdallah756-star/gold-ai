from paper.position import Position


class PositionManager:

    def __init__(self):
        self.positions = []

    def open_position(self, position: Position):
        self.positions.append(position)

    def get_open_positions(self):
        return [p for p in self.positions if p.is_open]

    def close_position(self, position: Position):
        position.is_open = False