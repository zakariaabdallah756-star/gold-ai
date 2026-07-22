from enum import Enum
from indicators.indicator_values import IndicatorValues


class MarketRegimeDetector:

    def detect(self, indicators: IndicatorValues) -> MarketRegime:
        if (
            indicators.adx is None
            or indicators.atr is None
        ):
            return MarketRegime.UNKNOWN

        breakout_up = (
            indicators.current_close is not None
            and indicators.recent_high is not None
            and indicators.current_close > indicators.recent_high
        )

        breakout_down = (
            indicators.current_close is not None
            and indicators.recent_low is not None
            and indicators.current_close < indicators.recent_low
        )

        volume_confirmed = (
            indicators.current_volume is not None
            and indicators.average_volume is not None
            and indicators.current_volume > indicators.average_volume
        )

        if (
            (breakout_up or breakout_down)
            and volume_confirmed
            and indicators.adx >= 20
        ):
            return MarketRegime.BREAKOUT

        if indicators.adx >= 25:
            return MarketRegime.TREND

        if indicators.adx < 20:
            return MarketRegime.RANGE

        return MarketRegime.SCALPING


class MarketRegime(Enum):
    TREND = "TREND"
    BREAKOUT = "BREAKOUT"
    RANGE = "RANGE"
    SCALPING = "SCALPING"
    UNKNOWN = "UNKNOWN"