from indicators.indicator_values import IndicatorValues
from strategy.signal import Signal, SignalType


class ScalpingStrategyV2:

    MIN_ADX = 20.0
    MAX_ADX = 25.0

    BUY_RSI_MIN = 55.0
    BUY_RSI_MAX = 70.0

    SELL_RSI_MIN = 30.0
    SELL_RSI_MAX = 45.0

    MIN_VOLUME_RATIO = 1.05

    MIN_BODY_RATIO = 0.55
    MIN_CLOSE_LOCATION = 0.70

    MIN_CANDLE_RANGE_ATR = 0.30
    MAX_CANDLE_RANGE_ATR = 1.50

    def generate(
        self,
        indicators: IndicatorValues,
    ) -> Signal:

        required_values = (
            indicators.current_open,
            indicators.current_high,
            indicators.current_low,
            indicators.current_close,
            indicators.ema50,
            indicators.ema200,
            indicators.rsi,
            indicators.atr,
            indicators.adx,
            indicators.current_volume,
            indicators.average_volume,
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

        if indicators.atr <= 0:
            return Signal(
                signal=SignalType.HOLD,
                confidence=0.0,
            )

        # 1. Regime scalping
        if (
            indicators.adx < self.MIN_ADX
            or indicators.adx >= self.MAX_ADX
        ):
            return Signal(
                signal=SignalType.HOLD,
                confidence=0.20,
            )

        candle_range = (
            indicators.current_high
            - indicators.current_low
        )

        if candle_range <= 0:
            return Signal(
                signal=SignalType.HOLD,
                confidence=0.0,
            )

        # 2. Volatilità utile:
        # niente candela troppo debole
        # e niente spike eccessivo.
        candle_range_atr = (
            candle_range
            / indicators.atr
        )

        if not (
            self.MIN_CANDLE_RANGE_ATR
            <= candle_range_atr
            <= self.MAX_CANDLE_RANGE_ATR
        ):
            return Signal(
                signal=SignalType.HOLD,
                confidence=0.20,
            )

        # 3. Volume
        volume_ratio = (
            indicators.current_volume
            / indicators.average_volume
            if indicators.average_volume > 0
            else 0.0
        )

        if (
            volume_ratio
            < self.MIN_VOLUME_RATIO
        ):
            return Signal(
                signal=SignalType.HOLD,
                confidence=0.20,
            )

        # 4. Qualità candela
        candle_body = abs(
            indicators.current_close
            - indicators.current_open
        )

        body_ratio = (
            candle_body
            / candle_range
        )

        if (
            body_ratio
            < self.MIN_BODY_RATIO
        ):
            return Signal(
                signal=SignalType.HOLD,
                confidence=0.20,
            )

        bullish_close_location = (
            indicators.current_close
            - indicators.current_low
        ) / candle_range

        bearish_close_location = (
            indicators.current_high
            - indicators.current_close
        ) / candle_range

        # BUY
        confirmed_buy = (
            indicators.ema50
            > indicators.ema200

            and indicators.current_close
            > indicators.ema50

            and self.BUY_RSI_MIN
            <= indicators.rsi
            <= self.BUY_RSI_MAX

            and indicators.current_close
            > indicators.current_open

            and bullish_close_location
            >= self.MIN_CLOSE_LOCATION

            and indicators.current_close
            < indicators.bollinger_upper
        )

        if confirmed_buy:
            return Signal(
                signal=SignalType.BUY,
                confidence=0.95,
            )

        # SELL
        confirmed_sell = (
            indicators.ema50
            < indicators.ema200

            and indicators.current_close
            < indicators.ema50

            and self.SELL_RSI_MIN
            <= indicators.rsi
            <= self.SELL_RSI_MAX

            and indicators.current_close
            < indicators.current_open

            and bearish_close_location
            >= self.MIN_CLOSE_LOCATION

            and indicators.current_close
            > indicators.bollinger_lower
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