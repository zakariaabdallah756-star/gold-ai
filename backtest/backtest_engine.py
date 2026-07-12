from market.data_engine import DataEngine
from strategy.strategy_engine import StrategyEngine
from indicators.indicator_engine import IndicatorEngine
from strategy.strategy_engine import StrategyEngine
from risk.risk_engine import RiskEngine
from backtest.statistics import BacktestStatistics
class BacktestEngine:

    def __init__(self, data_engine: DataEngine):
        self.data_engine = data_engine
        self.indicator_engine = IndicatorEngine()
        self.strategy_engine = StrategyEngine()
        self.signals = []
        self.indicators_history = []
        self.candles_history = []
        self.trades = []
        self.total_trades = 0
        self.buy_trades = 0
        self.sell_trades = 0
        self.last_signal = None

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
            self.last_signal = signal
            if signal.signal.value != "HOLD":
                self.trades.append(signal)
                self.total_trades += 1

                if signal.signal.value == "BUY":
                    self.buy_trades += 1

                if signal.signal.value == "SELL":
                    self.sell_trades += 1

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
    def get_total_trades(self):
        return self.total_trades
    def get_buy_trades(self):
        return self.buy_trades


    def get_sell_trades(self):
        return self.sell_trades
    def get_last_signal(self):
        return self.last_signal
    def get_statistics(self):
        total_profit = 0.0

        winning = 0
        losing = 0

        for trade in self.trades:
            if trade.signal.value == "BUY":
                winning += 1

            if trade.signal.value == "SELL":
                losing += 1

        total = winning + losing

        if total > 0:
            win_rate = (winning / total) * 100
        else:
            win_rate = 0.0

        open_trades = 0
        closed_trades = len(self.trades)
        if self.total_trades > 0:
            average_profit = total_profit / self.total_trades
        else:
            average_profit = 0.0

        return BacktestStatistics(
            total_trades=self.total_trades,
            buy_trades=self.buy_trades,
            sell_trades=self.sell_trades,
            winning_trades=winning,
            losing_trades=losing,
            total_profit=total_profit,
            win_rate=win_rate,
            open_trades=open_trades,
            closed_trades=closed_trades,
            average_profit=average_profit,
        )
    def reset(self):
        self.signals.clear()
        self.indicators_history.clear()
        self.candles_history.clear()
        self.trades.clear()

        self.total_trades = 0
        self.buy_trades = 0
        self.sell_trades = 0
        self.last_signal = None
    def execute(self):
        self.reset()
        self.run()
        return self.get_signals()    