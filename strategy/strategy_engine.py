from strategy.gold_strategy import GoldStrategy
from strategy.market_regime import MarketRegimeDetector, MarketRegime
from strategy.trend_following_strategy import TrendFollowingStrategy
from strategy.breakout_strategy import BreakoutStrategy
from strategy.mean_reversion_strategy import MeanReversionStrategy


class StrategyEngine:

    def __init__(self):
        self.strategy = GoldStrategy()
        self.market_regime_detector = MarketRegimeDetector()
        self.trend_strategy = TrendFollowingStrategy()
        self.breakout_strategy = BreakoutStrategy()
        self.mean_reversion_strategy = MeanReversionStrategy()

    def generate_signal(self, indicators):
        market_regime = self.market_regime_detector.detect(indicators)

        print("Market Regime:", market_regime.value)

        if market_regime == MarketRegime.TREND:
            return self.trend_strategy.generate(indicators)

        if market_regime == MarketRegime.BREAKOUT:
            return self.breakout_strategy.generate(indicators)

        if market_regime == MarketRegime.RANGE:
            return self.mean_reversion_strategy.generate(indicators)

        return self.strategy.generate(indicators)