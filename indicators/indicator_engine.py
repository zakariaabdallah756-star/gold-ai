from indicators.sma import SMA
from indicators.ema import EMA
from indicators.rsi import RSI


class IndicatorEngine:
    def __init__(self):
        self.sma = SMA()
        self.ema = EMA()
        self.rsi = RSI()

    def calculate(self, candles):
        return {
            "SMA": self.sma.calculate(candles, 1),
            "EMA": self.ema.calculate(candles, 1),
            "RSI": self.rsi.calculate(candles, 14),
        }