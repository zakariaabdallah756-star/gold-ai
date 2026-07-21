from indicators.sma import SMA
from indicators.ema import EMA
from indicators.rsi import RSI
from indicators.indicator_values import IndicatorValues
from indicators.atr import ATR
from indicators.adx import ADX
from indicators.bollinger_bands import BollingerBands

class IndicatorEngine:
    def __init__(self):
        self.sma = SMA()
        self.ema = EMA()
        self.rsi = RSI()
        self.atr = ATR()
        self.adx = ADX()
        self.bollinger_bands = BollingerBands()

    def calculate(self, candles):
        if not candles:
            return IndicatorValues()

        current_candle = candles[-1]

        recent_high = None
        recent_low = None
        average_volume = None

        if len(candles) >= 21:
            previous_candles = candles[-21:-1]

            recent_high = max(
                float(candle.high) for candle in previous_candles
            )

            recent_low = min(
                float(candle.low) for candle in previous_candles
            )

            average_volume = sum(
                float(candle.volume) for candle in previous_candles
            ) / len(previous_candles)

        bollinger_upper, bollinger_middle, bollinger_lower = (
            self.bollinger_bands.calculate(
                candles=candles,
                period=20,
                deviation=2.0,
            )
        )

        return IndicatorValues(
            ema50=self.ema.calculate(candles, 50),
            ema200=self.ema.calculate(candles, 200),
            rsi=self.rsi.calculate(candles, 14),
            atr=self.atr.calculate(candles, 14),
            adx=self.adx.calculate(candles, 14),
            current_close=float(current_candle.close),
            recent_high=recent_high,
            recent_low=recent_low,
            current_volume=float(current_candle.volume),
            average_volume=average_volume,
            bollinger_upper=bollinger_upper,
            bollinger_middle=bollinger_middle,
            bollinger_lower=bollinger_lower,
        )