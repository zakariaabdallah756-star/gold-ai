from indicators.sma import SMA
from indicators.ema import EMA
from indicators.rsi import RSI
from indicators.indicator_values import IndicatorValues
from indicators.atr import ATR
from indicators.adx import ADX

class IndicatorEngine:
    def __init__(self):
        self.sma = SMA()
        self.ema = EMA()
        self.rsi = RSI()
        self.atr = ATR()
        self.adx = ADX()

    def calculate(self, candles):
        return IndicatorValues(
            ema50=self.ema.calculate(candles, 50),
            ema200=self.ema.calculate(candles, 200),
            rsi=self.rsi.calculate(candles, 14),
            atr=self.atr.calculate(candles, 14),
            adx=self.adx.calculate(candles, 14),
        )