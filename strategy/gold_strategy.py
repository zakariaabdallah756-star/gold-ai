from strategy.signal import Signal, SignalType


class GoldStrategy:

    def generate(self, indicators):

        # Nessun dato disponibile
        if (
            indicators.ema50 is None
            or indicators.ema200 is None
            or indicators.rsi is None
        ):
            return Signal(
                signal=SignalType.HOLD,
                confidence=0.0,
            )

        # BUY
        if (
            indicators.ema50 > indicators.ema200
            and indicators.rsi > 55
        ):
            return Signal(
                signal=SignalType.BUY,
                confidence=0.90,
            )

        # SELL
        if (
            indicators.ema50 < indicators.ema200
            and indicators.rsi < 45
        ):
            return Signal(
                signal=SignalType.SELL,
                confidence=0.90,
            )

        return Signal(
            signal=SignalType.HOLD,
            confidence=0.50,
        )