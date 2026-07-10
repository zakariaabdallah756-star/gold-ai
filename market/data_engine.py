from market.repository import MarketRepository


class DataEngine:

    def __init__(self):
        self.repository = MarketRepository()

    def load(self):
        return self.repository.get_all()