from market.data_engine import DataEngine
from strategy.strategy_engine import StrategyEngine
from indicators.indicator_engine import IndicatorEngine
from strategy.strategy_engine import StrategyEngine
from risk.risk_engine import RiskEngine
from backtest.statistics import BacktestStatistics
from backtest.profit_calculator import BacktestProfitCalculator
from backtest.position_manager import BacktestPositionManager
from backtest.backtest_position import BacktestPosition
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
        self.total_profit = 0.0
        self.winning_trades = 0
        self.losing_trades = 0
        self.default_balance = 10000.0
        self.default_risk = 1.0
        self.default_stop_loss = 200.0
        self.default_pip_value = 1.0
        self.initial_balance = 10000.0

        self.risk_engine = RiskEngine()
        self.profit_calculator = BacktestProfitCalculator()
        self.position_manager = BacktestPositionManager()
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
                lot_size = self.risk_engine.calculate_position_size(
                    balance=self.default_balance,
                    risk_percent=self.default_risk,
                    stop_loss_pips=self.default_stop_loss,
                    pip_value=self.default_pip_value,
                )
                open_positions = self.position_manager.get_open_positions()

        for open_position in open_positions:
            if open_position.signal != signal.signal:

                profit = self.profit_calculator.calculate(
                    signal=open_position.signal,
                    entry_price=open_position.entry_price,
                    exit_price=candle.close,
                    lot_size=open_position.lot_size,
                )

                self.total_profit += profit
                if profit > 0:
                    self.winning_trades += 1
                elif profit < 0:
                    self.losing_trades += 1

                self.position_manager.close_position(open_position)

                position = BacktestPosition(
                    symbol="XAUUSD",
                    signal=signal.signal,
                    entry_price=candle.close,
                    lot_size=lot_size,
                )

                self.position_manager.open_position(position)

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
        total_profit = self.total_profit

        winning = self.winning_trades
        losing = self.losing_trades

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
            
        
        profit_factor = 0.0

        final_equity = self.initial_balance + total_profit
        if total_profit < 0:
            max_drawdown = abs(total_profit)
        else:
            max_drawdown = 0.0

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
            profit_factor=profit_factor,
            final_equity=final_equity,
            max_drawdown=max_drawdown,
        )
    def get_open_positions(self):
        return self.position_manager.get_open_positions()
    def reset(self):
        self.signals.clear()
        self.indicators_history.clear()
        self.candles_history.clear()
        self.trades.clear()

        self.total_trades = 0
        self.buy_trades = 0
        self.sell_trades = 0
        self.last_signal = None
        self.total_profit = 0.0
        self.winning_trades = 0
        self.losing_trades = 0
    def execute(self):
        self.reset()
        self.run()
        return self.get_signals()    