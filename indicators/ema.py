from data.candle import Candle


class EMA:
    def calculate(self, candles: list[Candle], period: int):
        if len(candles) < period:
            return None

        closes = [c.close for c in candles[-period:]]

        multiplier = 2 / (period + 1)

        ema = closes[0]

        for price in closes[1:]:
            ema = (price - ema) * multiplier + ema

        return ema