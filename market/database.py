from market.candle import Candle


class MarketDatabase:

    def __init__(self):
        self._candles: list[Candle] = []

    def insert(self, candle: Candle):
        self._candles.append(candle)

    def all(self) -> list[Candle]:
        return self._candles

    def clear(self):
        self._candles.clear()