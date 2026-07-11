from market.data_engine import DataEngine
from strategy.strategy_engine import StrategyEngine
from indicators.indicator_engine import IndicatorEngine
from strategy.strategy_engine import StrategyEngine
from risk.risk_engine import RiskEngine
class BacktestEngine:

    def __init__(self, data_engine: DataEngine):
        self.data_engine = data_engine
        self.indicator_engine = IndicatorEngine()
        self.strategy_engine = StrategyEngine()
        self.risk_engine = RiskEngine()
    def load_data(self):
        return self.data_engine.get_candles()
    def run(self):
        candles = self.load_data()

        if not candles:
            print("Nessuna candela disponibile.")
            return

        indicators = self.indicator_engine.calculate(candles)

        signal = self.strategy_engine.generate_signal(indicators)

        print("Indicators:", indicators)
        print("Signal:", signal)