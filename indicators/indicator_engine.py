from indicators.sma import SMA
from indicators.ema import EMA
from indicators.rsi import RSI
from indicators.indicator_values import IndicatorValues
from indicators.atr import ATR

class IndicatorEngine:
    def __init__(self):
        self.sma = SMA()
        self.ema = EMA()
        self.rsi = RSI()
        self.atr = ATR()

    def calculate(self, candles):
        return IndicatorValues(
            ema50=self.ema.calculate(candles, 50),
            ema200=self.ema.calculate(candles, 200),
            rsi=self.rsi.calculate(candles, 14),
            atr=self.atr.calculate(candles, 14),
        )