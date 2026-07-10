from market.data_engine import DataEngine


class BacktestEngine:

    def __init__(self, data_engine: DataEngine):
        self.data_engine = data_engine

    def load_data(self):
        return self.data_engine.get_candles()
    def run(self):
        candles = self.load_data()
        for candle in candles:
            print(candle)