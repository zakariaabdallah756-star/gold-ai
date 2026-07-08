from data.candle import Candle


class SMA:
    def calculate(self, candles: list[Candle], period: int):
        if len(candles) < period:
            return None

        closes = [c.close for c in candles[-period:]]

        return sum(closes) / period