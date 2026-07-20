from indicators.indicator_values import IndicatorValues
from strategy.signal import Signal, SignalType


class BreakoutStrategy:

    def generate(self, indicators: IndicatorValues) -> Signal:
        if (
            indicators.current_close is None
            or indicators.recent_high is None
            or indicators.recent_low is None
            or indicators.current_volume is None
            or indicators.average_volume is None
            or indicators.atr is None
            or indicators.adx is None
        ):
            return Signal(
                signal=SignalType.HOLD,
                confidence=0.0,
            )

        volume_confirmed = (
            indicators.current_volume > indicators.average_volume
        )

        volatility_confirmed = (
            indicators.atr > 0
            and indicators.adx >= 20
        )

        if (
            indicators.current_close > indicators.recent_high
            and volume_confirmed
            and volatility_confirmed
        ):
            return Signal(
                signal=SignalType.BUY,
                confidence=0.85,
            )

        if (
            indicators.current_close < indicators.recent_low
            and volume_confirmed
            and volatility_confirmed
        ):
            return Signal(
                signal=SignalType.SELL,
                confidence=0.85,
            )

        return Signal(
            signal=SignalType.HOLD,
            confidence=0.50,
        )