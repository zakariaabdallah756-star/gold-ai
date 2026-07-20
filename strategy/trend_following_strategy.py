from indicators.indicator_values import IndicatorValues
from strategy.signal import Signal, SignalType


class TrendFollowingStrategy:

    def generate(self, indicators: IndicatorValues) -> Signal:
        if (
            indicators.ema50 is None
            or indicators.ema200 is None
            or indicators.rsi is None
            or indicators.adx is None
        ):
            return Signal(
                signal=SignalType.HOLD,
                confidence=0.0,
            )

        if indicators.adx < 25:
            return Signal(
                signal=SignalType.HOLD,
                confidence=0.40,
            )

        if (
            indicators.ema50 > indicators.ema200
            and indicators.rsi >= 55
        ):
            return Signal(
                signal=SignalType.BUY,
                confidence=0.90,
            )

        if (
            indicators.ema50 < indicators.ema200
            and indicators.rsi <= 45
        ):
            return Signal(
                signal=SignalType.SELL,
                confidence=0.90,
            )

        return Signal(
            signal=SignalType.HOLD,
            confidence=0.50,
        )