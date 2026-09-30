from indicators.indicator_values import IndicatorValues
from strategy.signal import Signal, SignalType


class BreakoutStrategyV3:

    MAX_BOLLINGER_WIDTH_RATIO = 0.01

    MIN_VOLUME_RATIO = 1.20
    MIN_ADX = 25

    MIN_BODY_RATIO = 0.60
    MIN_CLOSE_LOCATION = 0.75

    MIN_BREAKOUT_ATR_RATIO = 0.10
    MAX_BREAKOUT_ATR_RATIO = 0.80

    BUY_RSI_MIN = 55
    SELL_RSI_MAX = 45

    def generate(
        self,
        indicators: IndicatorValues,
    ) -> Signal:

        required_values = (
            indicators.current_open,
            indicators.current_high,
            indicators.current_low,
            indicators.current_close,
            indicators.recent_high,
            indicators.recent_low,
            indicators.current_volume,
            indicators.average_volume,
            indicators.atr,
            indicators.adx,
            indicators.ema50,
            indicators.ema200,
            indicators.rsi,
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

        if (
            indicators.current_close <= 0
            or indicators.atr <= 0
        ):
            return Signal(
                signal=SignalType.HOLD,
                confidence=0.0,
            )

        # 1. Consolidamento
        bollinger_width = (
            indicators.bollinger_upper
            - indicators.bollinger_lower
        )

        bollinger_width_ratio = (
            bollinger_width
            / indicators.current_close
        )

        if (
            bollinger_width_ratio
            > self.MAX_BOLLINGER_WIDTH_RATIO
        ):
            return Signal(
                signal=SignalType.HOLD,
                confidence=0.30,
            )

        # 2. Momentum ADX
        if indicators.adx < self.MIN_ADX:
            return Signal(
                signal=SignalType.HOLD,
                confidence=0.30,
            )

        # 3. Volume
        minimum_volume = (
            indicators.average_volume
            * self.MIN_VOLUME_RATIO
        )

        if (
            indicators.current_volume
            < minimum_volume
        ):
            return Signal(
                signal=SignalType.HOLD,
                confidence=0.30,
            )

        # 4. Qualità candela
        candle_range = (
            indicators.current_high
            - indicators.current_low
        )

        if candle_range <= 0:
            return Signal(
                signal=SignalType.HOLD,
                confidence=0.0,
            )

        candle_body = abs(
            indicators.current_close
            - indicators.current_open
        )

        body_ratio = (
            candle_body
            / candle_range
        )

        if body_ratio < self.MIN_BODY_RATIO:
            return Signal(
                signal=SignalType.HOLD,
                confidence=0.30,
            )

        bullish_close_location = (
            indicators.current_close
            - indicators.current_low
        ) / candle_range

        bearish_close_location = (
            indicators.current_high
            - indicators.current_close
        ) / candle_range

        # 5. BUY
        bullish_breakout = (
            indicators.current_close
            > indicators.recent_high
        )

        if bullish_breakout:

            breakout_distance = (
                indicators.current_close
                - indicators.recent_high
            )

            breakout_atr_ratio = (
                breakout_distance
                / indicators.atr
            )

            confirmed_buy = (
                indicators.current_close
                > indicators.bollinger_upper
                and indicators.ema50
                > indicators.ema200
                and indicators.current_close
                > indicators.ema50
                and indicators.rsi
                >= self.BUY_RSI_MIN
                and indicators.current_close
                > indicators.current_open
                and bullish_close_location
                >= self.MIN_CLOSE_LOCATION
                and breakout_atr_ratio
                >= self.MIN_BREAKOUT_ATR_RATIO
                and breakout_atr_ratio
                <= self.MAX_BREAKOUT_ATR_RATIO
            )

            if confirmed_buy:
                return Signal(
                    signal=SignalType.BUY,
                    confidence=0.95,
                )

        # 6. SELL
        bearish_breakout = (
            indicators.current_close
            < indicators.recent_low
        )

        if bearish_breakout:

            breakout_distance = (
                indicators.recent_low
                - indicators.current_close
            )

            breakout_atr_ratio = (
                breakout_distance
                / indicators.atr
            )

            confirmed_sell = (
                indicators.current_close
                < indicators.bollinger_lower
                and indicators.ema50
                < indicators.ema200
                and indicators.current_close
                < indicators.ema50
                and indicators.rsi
                <= self.SELL_RSI_MAX
                and indicators.current_close
                < indicators.current_open
                and bearish_close_location
                >= self.MIN_CLOSE_LOCATION
                and breakout_atr_ratio
                >= self.MIN_BREAKOUT_ATR_RATIO
                and breakout_atr_ratio
                <= self.MAX_BREAKOUT_ATR_RATIO
            )

            if confirmed_sell:
                return Signal(
                    signal=SignalType.SELL,
                    confidence=0.95,
                )

        return Signal(
            signal=SignalType.HOLD,
            confidence=0.20,
        )