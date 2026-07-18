from enum import Enum
from indicators.indicator_values import IndicatorValues


class MarketRegimeDetector:

    def detect(self, indicators: IndicatorValues) -> MarketRegime:

        if (
            indicators.adx is None
            or indicators.atr is None
            or indicators.rsi is None
        ):
            return MarketRegime.UNKNOWN

        if indicators.adx >= 25:
            return MarketRegime.TREND

        if indicators.atr >= 20:
            return MarketRegime.BREAKOUT

        if 40 <= indicators.rsi <= 60:
            return MarketRegime.RANGE

        return MarketRegime.SCALPING


class MarketRegime(Enum):
    TREND = "TREND"
    BREAKOUT = "BREAKOUT"
    RANGE = "RANGE"
    SCALPING = "SCALPING"
    UNKNOWN = "UNKNOWN"