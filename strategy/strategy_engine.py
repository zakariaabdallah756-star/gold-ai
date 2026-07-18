from strategy.gold_strategy import GoldStrategy
from strategy.market_regime import MarketRegimeDetector, MarketRegime


class StrategyEngine:

    def __init__(self):
        self.strategy = GoldStrategy()
        self.market_regime_detector = MarketRegimeDetector()

    def generate_signal(self, indicators):
        market_regime = self.market_regime_detector.detect(indicators)

        print("Market Regime:", market_regime.value)

        return self.strategy.generate(indicators)