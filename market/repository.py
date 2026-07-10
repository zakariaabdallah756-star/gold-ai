from market.candle import Candle
from market.database import MarketDatabase


class MarketRepository:

    def __init__(self):
        self.database = MarketDatabase()

    def add(self, candle: Candle):
        self.database.insert(candle)

    def get_all(self) -> list[Candle]:
        return self.database.all()

    def clear(self):
        self.database.clear()