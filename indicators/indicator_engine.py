from indicators.sma import SMA
from indicators.ema import EMA
from indicators.rsi import RSI
from indicators.indicator_values import IndicatorValues

class IndicatorEngine:
    def __init__(self):
        self.sma = SMA()
        self.ema = EMA()
        self.rsi = RSI()

    def calculate(self, candles):
       return IndicatorValues(
            sma=self.sma.calculate(candles, 1),
            ema=self.ema.calculate(candles, 1),
            rsi=self.rsi.calculate(candles, 14)
        )