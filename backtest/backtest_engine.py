from market.data_engine import DataEngine


class BacktestEngine:

    def __init__(self):
        self.data_engine = DataEngine()

    def load_data(self):
        return self.data_engine.repository.get_all()