from indicators.indicator_values import IndicatorValues
from strategy.signal import Signal, SignalType


class BreakoutStrategyV2:

    def __init__(
        self,
        use_candle_confirmation: bool = True,
    ):
        self.use_candle_confirmation = (
            use_candle_confirmation
        )

    def generate(
        self,
        indicators: IndicatorValues,
    ) -> Signal:

        required_values = (
            indicators.current_close,
            indicators.recent_high,
            indicators.recent_low,
            indicators.current_volume,
            indicators.average_volume,
            indicators.atr,
            indicators.adx,
            indicators.bollinger_upper,
            indicators.bollinger_lower,
        )

        if any(
            value is None
            for value in required_values
        ):
            return Signal(
                signal=SignalType.HOLD,
                confidence=0.0,
            )

        if self.use_candle_confirmation:
            candle_values = (
                indicators.current_open,
                indicators.current_high,
                indicators.current_low,
            )

            if any(
                value is None
                for value in candle_values
            ):
                return Signal(
                    signal=SignalType.HOLD,
                    confidence=0.0,
                )

        volume_confirmed = (
            indicators.current_volume
            > indicators.average_volume
        )

        volatility_confirmed = (
            indicators.atr > 0
            and indicators.adx >= 20
        )

        bollinger_width = (
            indicators.bollinger_upper
            - indicators.bollinger_lower
        )

        consolidation = (
            indicators.current_close > 0
            and (
                bollinger_width
                / indicators.current_close
            ) <= 0.01
        )

        bullish_breakout = (
            indicators.current_close
            > indicators.recent_high
        )

        bearish_breakout = (
            indicators.current_close
            < indicators.recent_low
        )

        bullish_candle_confirmed = True
        bearish_candle_confirmed = True

        if self.use_candle_confirmation:
            candle_range = (
                indicators.current_high
                - indicators.current_low
            )

            candle_body = abs(
                indicators.current_close
                - indicators.current_open
            )

            strong_candle = (
                candle_range > 0
                and (
                    candle_body
                    / candle_range
                ) >= 0.50
            )

            bullish_candle_confirmed = (
                indicators.current_close
                > indicators.current_open
                and strong_candle
            )

            bearish_candle_confirmed = (
                indicators.current_close
                < indicators.current_open
                and strong_candle
            )

        if (
            consolidation
            and bullish_breakout
            and volume_confirmed
            and volatility_confirmed
            and bullish_candle_confirmed
        ):
            return Signal(
                signal=SignalType.BUY,
                confidence=0.90,
            )

        if (
            consolidation
            and bearish_breakout
            and volume_confirmed
            and volatility_confirmed
            and bearish_candle_confirmed
        ):
            return Signal(
                signal=SignalType.SELL,
                confidence=0.90,
            )

        return Signal(
            signal=SignalType.HOLD,
            confidence=0.50,
        )