from indicators.indicator_values import IndicatorValues
from strategy.signal import Signal, SignalType


class ScalpingStrategy:

    def generate(self, indicators: IndicatorValues) -> Signal:
        if (
            indicators.current_close is None
            or indicators.ema50 is None
            or indicators.rsi is None
            or indicators.atr is None
            or indicators.adx is None
        ):
            return Signal(
                signal=SignalType.HOLD,
                confidence=0.0,
            )

        # Evita mercati troppo piatti o trend troppo forti
        if indicators.adx < 20 or indicators.adx >= 25:
            return Signal(
                signal=SignalType.HOLD,
                confidence=0.30,
            )

        if indicators.atr <= 0:
            return Signal(
                signal=SignalType.HOLD,
                confidence=0.30,
            )

        if (
            indicators.current_close > indicators.ema50
            and indicators.rsi >= 55
        ):
            return Signal(
                signal=SignalType.BUY,
                confidence=0.70,
            )

        if (
            indicators.current_close < indicators.ema50
            and indicators.rsi <= 45
        ):
            return Signal(
                signal=SignalType.SELL,
                confidence=0.70,
            )

        return Signal(
            signal=SignalType.HOLD,
            confidence=0.50,
        )