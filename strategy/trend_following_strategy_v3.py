from indicators.indicator_values import IndicatorValues
from strategy.signal import Signal, SignalType


class TrendFollowingStrategyV3:

    EMA_DISTANCE_ATR_RATIO = 0.50

    def generate(
        self,
        indicators: IndicatorValues,
    ) -> Signal:

        required_values = (
            indicators.ema50,
            indicators.ema200,
            indicators.rsi,
            indicators.adx,
            indicators.atr,
            indicators.current_close,
        )

        if any(
            value is None
            for value in required_values
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

        if indicators.atr <= 0:
            return Signal(
                signal=SignalType.HOLD,
                confidence=0.0,
            )

        ema_distance = abs(
            indicators.ema50
            - indicators.ema200
        )

        trend_separation_confirmed = (
            ema_distance
            >= (
                indicators.atr
                * self.EMA_DISTANCE_ATR_RATIO
            )
        )

        if not trend_separation_confirmed:
            return Signal(
                signal=SignalType.HOLD,
                confidence=0.40,
            )

        if (
            indicators.ema50 > indicators.ema200
            and indicators.current_close > indicators.ema50
            and indicators.rsi >= 55
        ):
            return Signal(
                signal=SignalType.BUY,
                confidence=0.90,
            )

        if (
            indicators.ema50 < indicators.ema200
            and indicators.current_close < indicators.ema50
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