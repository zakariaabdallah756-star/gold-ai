from indicators.indicator_values import IndicatorValues
from strategy.signal import Signal, SignalType


class MeanReversionStrategyV2:

    MAX_ADX = 18.0

    BUY_RSI_MAX = 32.0
    SELL_RSI_MIN = 68.0

    MIN_CLOSE_LOCATION = 0.65

    MIN_BAND_EXCURSION_ATR = 0.05
    MAX_BAND_EXCURSION_ATR = 0.75

    MAX_CANDLE_RANGE_ATR = 2.0

    MAX_EMA_SEPARATION_ATR = 0.50

    def generate(
        self,
        indicators: IndicatorValues,
    ) -> Signal:

        required_values = (
            indicators.current_open,
            indicators.current_high,
            indicators.current_low,
            indicators.current_close,
            indicators.bollinger_upper,
            indicators.bollinger_middle,
            indicators.bollinger_lower,
            indicators.rsi,
            indicators.adx,
            indicators.atr,
            indicators.ema50,
            indicators.ema200,
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

        # 1. Mercato realmente laterale
        if indicators.adx > self.MAX_ADX:
            return Signal(
                signal=SignalType.HOLD,
                confidence=0.20,
            )

        # 2. Evita trend nascosti
        ema_separation = abs(
            indicators.ema50
            - indicators.ema200
        )

        ema_separation_atr = (
            ema_separation
            / indicators.atr
        )

        if (
            ema_separation_atr
            > self.MAX_EMA_SEPARATION_ATR
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

        # 3. Evita candele anomale / news spike
        candle_range_atr = (
            candle_range
            / indicators.atr
        )

        if (
            candle_range_atr
            > self.MAX_CANDLE_RANGE_ATR
        ):
            return Signal(
                signal=SignalType.HOLD,
                confidence=0.20,
            )

        # Posizione della chiusura nella candela
        bullish_close_location = (
            indicators.current_close
            - indicators.current_low
        ) / candle_range

        bearish_close_location = (
            indicators.current_high
            - indicators.current_close
        ) / candle_range

        # 4. BUY:
        # il minimo supera la banda inferiore,
        # ma la chiusura rientra dentro.
        buy_rejection = (
            indicators.current_low
            < indicators.bollinger_lower
            and indicators.current_close
            > indicators.bollinger_lower
        )

        if buy_rejection:

            excursion = (
                indicators.bollinger_lower
                - indicators.current_low
            )

            excursion_atr = (
                excursion
                / indicators.atr
            )

            confirmed_buy = (
                indicators.rsi
                <= self.BUY_RSI_MAX

                and indicators.current_close
                > indicators.current_open

                and bullish_close_location
                >= self.MIN_CLOSE_LOCATION

                and excursion_atr
                >= self.MIN_BAND_EXCURSION_ATR

                and excursion_atr
                <= self.MAX_BAND_EXCURSION_ATR

                and indicators.current_close
                < indicators.bollinger_middle
            )

            if confirmed_buy:
                return Signal(
                    signal=SignalType.BUY,
                    confidence=0.95,
                )

        # 5. SELL:
        # il massimo supera la banda superiore,
        # ma la chiusura rientra dentro.
        sell_rejection = (
            indicators.current_high
            > indicators.bollinger_upper
            and indicators.current_close
            < indicators.bollinger_upper
        )

        if sell_rejection:

            excursion = (
                indicators.current_high
                - indicators.bollinger_upper
            )

            excursion_atr = (
                excursion
                / indicators.atr
            )

            confirmed_sell = (
                indicators.rsi
                >= self.SELL_RSI_MIN

                and indicators.current_close
                < indicators.current_open

                and bearish_close_location
                >= self.MIN_CLOSE_LOCATION

                and excursion_atr
                >= self.MIN_BAND_EXCURSION_ATR

                and excursion_atr
                <= self.MAX_BAND_EXCURSION_ATR

                and indicators.current_close
                > indicators.bollinger_middle
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