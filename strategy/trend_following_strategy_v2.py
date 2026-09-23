from indicators.indicator_values import IndicatorValues
from strategy.signal import Signal, SignalType


class TrendFollowingStrategyV2:

    def generate(
        self,
        indicators: IndicatorValues,
    ) -> Signal:

        required_values = (
            indicators.ema50,
            indicators.ema200,
            indicators.rsi,
            indicators.adx,
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

        bullish_trend = (
            indicators.ema50
            > indicators.ema200
        )

        bearish_trend = (
            indicators.ema50
            < indicators.ema200
        )

        bullish_price_confirmed = (
            indicators.current_close
            > indicators.ema50
        )

        bearish_price_confirmed = (
            indicators.current_close
            < indicators.ema50
        )

        if (
            bullish_trend
            and bullish_price_confirmed
            and indicators.rsi >= 55
        ):
            return Signal(
                signal=SignalType.BUY,
                confidence=0.90,
            )

        if (
            bearish_trend
            and bearish_price_confirmed
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