from indicators.indicator_values import IndicatorValues
from strategy.signal import Signal, SignalType


class MeanReversionStrategyV3:

    MAX_ADX = 20.0

    BUY_RSI_MAX = 35.0
    SELL_RSI_MIN = 65.0

    MIN_CLOSE_LOCATION = 0.60

    MAX_CANDLE_RANGE_ATR = 2.50

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
            indicators.bollinger_lower,
            indicators.rsi,
            indicators.adx,
            indicators.atr,
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

        # 1. Regime RANGE obbligatorio
        if indicators.adx >= self.MAX_ADX:
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

        # 2. Evita spike e candele anomale
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

        bullish_close_location = (
            indicators.current_close
            - indicators.current_low
        ) / candle_range

        bearish_close_location = (
            indicators.current_high
            - indicators.current_close
        ) / candle_range

        # BUY
        #
        # Conferme obbligatorie:
        # - mercato range
        # - prezzo attraversa banda inferiore
        # - prezzo rientra nella banda
        # - RSI oversold
        # - candela bullish
        # - chiusura forte nella parte alta
        buy_confirmed = (
            indicators.current_low
            < indicators.bollinger_lower

            and indicators.current_close
            > indicators.bollinger_lower

            and indicators.rsi
            <= self.BUY_RSI_MAX

            and indicators.current_close
            > indicators.current_open

            and bullish_close_location
            >= self.MIN_CLOSE_LOCATION
        )

        if buy_confirmed:
            return Signal(
                signal=SignalType.BUY,
                confidence=0.95,
            )

        # SELL
        #
        # Conferme obbligatorie:
        # - mercato range
        # - prezzo attraversa banda superiore
        # - prezzo rientra nella banda
        # - RSI overbought
        # - candela bearish
        # - chiusura forte nella parte bassa
        sell_confirmed = (
            indicators.current_high
            > indicators.bollinger_upper

            and indicators.current_close
            < indicators.bollinger_upper

            and indicators.rsi
            >= self.SELL_RSI_MIN

            and indicators.current_close
            < indicators.current_open

            and bearish_close_location
            >= self.MIN_CLOSE_LOCATION
        )

        if sell_confirmed:
            return Signal(
                signal=SignalType.SELL,
                confidence=0.95,
            )

        return Signal(
            signal=SignalType.HOLD,
            confidence=0.20,
        )