from paper.position import Position


class ProfitLossCalculator:

    def calculate(self, position: Position, current_price: float):
        if position.signal.value == "BUY":
            return (current_price - position.entry_price) * position.lot_size

        if position.signal.value == "SELL":
            return (position.entry_price - current_price) * position.lot_size

        return 0.0