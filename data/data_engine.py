from data.market_stream import MarketStream
from data.candle_repository import CandleRepository


class DataEngine:
    def __init__(self):
        self.stream = MarketStream()
        self.repository = CandleRepository()

    def update(self):
        candle = self.stream.get_next_candle()
        self.repository.add(candle)
        return candle