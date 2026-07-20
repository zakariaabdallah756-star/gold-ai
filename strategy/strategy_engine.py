from strategy.gold_strategy import GoldStrategy
from strategy.market_regime import MarketRegimeDetector, MarketRegime
from strategy.trend_following_strategy import TrendFollowingStrategy


class StrategyEngine:

    def __init__(self):
        self.strategy = GoldStrategy()
        self.market_regime_detector = MarketRegimeDetector()
        self.trend_strategy = TrendFollowingStrategy()

    def generate_signal(self, indicators):
        market_regime = self.market_regime_detector.detect(indicators)

        print("Market Regime:", market_regime.value)

        if market_regime == MarketRegime.TREND:
            return self.trend_strategy.generate(indicators)

        return self.strategy.generate(indicators)