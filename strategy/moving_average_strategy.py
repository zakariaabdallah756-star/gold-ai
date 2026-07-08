from strategy.signal import Signal, SignalType


class MovingAverageStrategy:

    def generate(self, indicators):
        if indicators.sma is None or indicators.ema is None:
            return Signal(signal=SignalType.HOLD, confidence=0.0)

        if indicators.sma > indicators.ema:
            return Signal(signal=SignalType.BUY, confidence=0.80)

        if indicators.sma < indicators.ema:
            return Signal(signal=SignalType.SELL, confidence=0.80)

        return Signal(signal=SignalType.HOLD, confidence=0.50)