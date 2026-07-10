from market.candle import Candle
from market.market_stream import MarketStream
from market.candle_validator import CandleValidator
from market.repository import MarketRepository


class DataEngine:

    def __init__(self):
        self.repository = MarketRepository()
        self.stream = MarketStream()
        self.validator = CandleValidator()

    def add_candle(self, candle: Candle):
        if self.validator.validate(candle):
            self.repository.add(candle)

    def update(self):
        candle = self.stream.get_next_candle()

        if self.validator.validate(candle):
            self.repository.add(candle)
            return candle

        return None

    def get_candles(self):
        return self.repository.get_all()

    def clear(self):
        self.repository.clear()