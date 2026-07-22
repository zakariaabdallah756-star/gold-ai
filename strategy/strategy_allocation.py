from strategy.market_regime import MarketRegime


class StrategyAllocation:

    def get_weight(self, market_regime: MarketRegime) -> float:
        if market_regime == MarketRegime.TREND:
            return 0.50

        if market_regime == MarketRegime.BREAKOUT:
            return 0.25

        if market_regime == MarketRegime.RANGE:
            return 0.15

        if market_regime == MarketRegime.SCALPING:
            return 0.10

        return 0.0