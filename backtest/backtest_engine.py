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
        self.signals = []
        self.indicators_history = []
        self.candles_history = []
        self.trades = []
        self.risk_engine = RiskEngine()
    def load_data(self):
        return self.data_engine.get_candles()
    def run(self):
        candles = self.load_data()

        if not candles:
            print("Nessuna candela disponibile.")
            return

        history = []

        for candle in candles:
            history.append(candle)
            self.candles_history.append(candle)

            indicators = self.indicator_engine.calculate(history)
            self.indicators_history.append(indicators)

            signal = self.strategy_engine.generate_signal(indicators)

            self.signals.append(signal)

            if signal.signal.value != "HOLD":
                self.trades.append(signal)

            print(candle)
            print("Indicators:", indicators)
            print("Signal:", signal)
    def get_signals(self):
        return self.signals
    def get_indicators(self):
        return self.indicators_history
    def get_candles(self):
        return self.candles_history
    def get_trades(self):
        return self.trades
    
    def reset(self):
        self.signals.clear()
        self.indicators_history.clear()
        self.candles_history.clear()
        self.trades.clear()
    def execute(self):
        self.reset()
        self.run()
        return self.get_signals()    