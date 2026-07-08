from data.market_stream import MarketStream
from data.candle_repository import CandleRepository
from data.validator import CandleValidator

class DataEngine:
    def __init__(self):
        self.stream = MarketStream()
        self.repository = CandleRepository()
        self.validator = CandleValidator()

    def update(self):
        candle = self.stream.get_next_candle()

        if self.validator.validate(candle):
            self.repository.add(candle)
            return candle

        return None