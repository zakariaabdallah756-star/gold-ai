from indicators.indicator_values import IndicatorValues
from strategy.signal import Signal, SignalType


class MeanReversionStrategy:

    def generate(self, indicators: IndicatorValues) -> Signal:
        if (
            indicators.current_close is None
            or indicators.bollinger_upper is None
            or indicators.bollinger_lower is None
            or indicators.rsi is None
            or indicators.adx is None
        ):
            return Signal(
                signal=SignalType.HOLD,
                confidence=0.0,
            )

        # La Mean Reversion non opera durante trend forti
        if indicators.adx >= 25:
            return Signal(
                signal=SignalType.HOLD,
                confidence=0.30,
            )

        if (
            indicators.current_close <= indicators.bollinger_lower
            and indicators.rsi <= 35
        ):
            return Signal(
                signal=SignalType.BUY,
                confidence=0.80,
            )

        if (
            indicators.current_close >= indicators.bollinger_upper
            and indicators.rsi >= 65
        ):
            return Signal(
                signal=SignalType.SELL,
                confidence=0.80,
            )

        return Signal(
            signal=SignalType.HOLD,
            confidence=0.50,
        )