from strategy.moving_average_strategy import MovingAverageStrategy


class StrategyEngine:
    def __init__(self):
        self.strategy = MovingAverageStrategy()

    def generate_signal(self, indicators):
        return self.strategy.generate(indicators)