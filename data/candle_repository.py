from data.candle import Candle


class CandleRepository:
    def __init__(self):
        self._candles = []

    def add(self, candle: Candle):
        self._candles.append(candle)

    def get_all(self):
        return self._candles

    def last(self):
        if self._candles:
            return self._candles[-1]
        return None