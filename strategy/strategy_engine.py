from strategy.gold_strategy import GoldStrategy


class StrategyEngine:

    def __init__(self):
        self.strategy = GoldStrategy()

    def generate_signal(self, indicators):
        return self.strategy.generate(indicators)