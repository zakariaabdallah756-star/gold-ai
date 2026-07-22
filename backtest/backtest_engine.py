import signal

from market.data_engine import DataEngine
from strategy.strategy_engine import StrategyEngine
from indicators.indicator_engine import IndicatorEngine
from strategy.strategy_engine import StrategyEngine
from risk.risk_engine import RiskEngine
from backtest.statistics import BacktestStatistics
from backtest.profit_calculator import BacktestProfitCalculator
from backtest.position_manager import BacktestPositionManager
from backtest.backtest_position import BacktestPosition
from strategy.signal import SignalType
from strategy.strategy_allocation import StrategyAllocation
from risk.atr_risk_manager import ATRRiskManager
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
        self.strategy_allocation = StrategyAllocation()
        self.atr_risk_manager = ATRRiskManager()
    def load_data(self):
        return self.data_engine.get_candles()
    def run(self):
        candles = self.load_data()

        if not candles:
            print("Nessuna candela disponibile.")
            return

        history = []

        for index, candle in enumerate(candles):
            # --- PIANO 21: Controllo Chiusura Reale SL / TP ---
            open_positions = list(
                self.position_manager.get_open_positions()
            )
            for open_position in open_positions:
                exit_price = None
                if open_position.signal == SignalType.BUY:
                    if float(candle.low) <= open_position.stop_loss:
                        exit_price = open_position.stop_loss
                    elif float(candle.high) >= open_position.take_profit:
                        exit_price = open_position.take_profit
                elif open_position.signal == SignalType.SELL:
                    if float(candle.high) >= open_position.stop_loss:
                        exit_price = open_position.stop_loss
                    elif float(candle.low) <= open_position.take_profit:
                        exit_price = open_position.take_profit

                if exit_price is not None:
                    profit = self.profit_calculator.calculate(
                        signal=open_position.signal,
                        entry_price=open_position.entry_price,
                        exit_price=exit_price,
                        lot_size=open_position.lot_size,
                    )
                    open_position.exit_price = exit_price
                    open_position.profit = profit
                    self.total_profit += profit

                    if profit > 0:
                        self.winning_trades += 1
                    elif profit < 0:
                        self.losing_trades += 1

                    self.position_manager.close_position(open_position)
                    print("SL/TP position closed")
                    print("Exit Price:", exit_price)
                    print("Profit:", profit)
            # -------------------------------------------------

            history.append(candle)
            self.candles_history.append(candle)

            indicators = self.indicator_engine.calculate(history)
            self.indicators_history.append(indicators)

            signal = self.strategy_engine.generate_signal(indicators)

            self.signals.append(signal)
            self.last_signal = signal

            print(candle)
            print("Indicators:", indicators)
            print("Signal:", signal)

            if signal.signal == SignalType.HOLD:
                continue

            market_regime = self.strategy_engine.get_last_market_regime()

            strategy_weight = self.strategy_allocation.get_weight(
                market_regime
            )

            allocated_risk = self.default_risk * strategy_weight

            if allocated_risk <= 0:
                continue

            if indicators.atr is None:
                continue

            entry_price = float(candle.close)

            stop_loss, take_profit = (
                self.atr_risk_manager.calculate_levels(
                    entry_price=entry_price,
                    atr=float(indicators.atr),
                    signal=signal.signal,
                )
            )

            if stop_loss is None or take_profit is None:
                continue

            stop_distance = abs(entry_price - stop_loss)

            lot_size = (
                self.risk_engine.calculate_position_size_from_distance(
                    balance=self.default_balance,
                    risk_percent=allocated_risk,
                    stop_distance=stop_distance,
                    value_per_price_unit=self.default_pip_value,
                )
            )

            if lot_size <= 0:
                continue

            print("Strategy Weight:", strategy_weight)
            print("Allocated Risk:", allocated_risk)
            print("Stop Distance:", stop_distance)
            print("Lot Size:", lot_size)
            print("Strategy Weight:", strategy_weight)
            print("Allocated Risk:", allocated_risk)
            print("Lot Size:", lot_size)

            open_positions = list(
                self.position_manager.get_open_positions()
            )

            # Chiude una posizione quando arriva un segnale opposto
            for open_position in open_positions:
                if open_position.signal != signal.signal:
                    profit = self.profit_calculator.calculate(
                        signal=open_position.signal,
                        entry_price=open_position.entry_price,
                        exit_price=float(candle.close),
                        lot_size=open_position.lot_size,
                    )

                    open_position.exit_price = float(candle.close)
                    open_position.profit = profit

                    self.total_profit += profit

                    if profit > 0:
                        self.winning_trades += 1
                    elif profit < 0:
                        self.losing_trades += 1

                    self.position_manager.close_position(open_position)

                    print("Profit:", profit)
                    print("Position closed")

            # Controlla se esiste già una posizione dello stesso tipo
            already_open = any(
                open_position.signal == signal.signal
                for open_position
                in self.position_manager.get_open_positions()
            )

            if already_open:
                continue

            # Non apre una posizione senza ATR disponibile
            # Non apre una nuova posizione sull'ultima candela
            if index == len(candles) - 1:
                continue
            position = BacktestPosition(
                symbol="XAUUSD",
                signal=signal.signal,
                entry_price=float(candle.close),
                lot_size=lot_size,
                stop_loss=stop_loss,
                take_profit=take_profit,
            )

            self.position_manager.open_position(position)
            self.trades.append(position)

            self.total_trades += 1

            if signal.signal == SignalType.BUY:
                self.buy_trades += 1
            elif signal.signal == SignalType.SELL:
                self.sell_trades += 1
        final_price = float(candles[-1].close)

        remaining_positions = list(
            self.position_manager.get_open_positions()
        )

        for open_position in remaining_positions:
            profit = self.profit_calculator.calculate(
                signal=open_position.signal,
                entry_price=open_position.entry_price,
                exit_price=final_price,
                lot_size=open_position.lot_size,
            )

            open_position.exit_price = final_price
            open_position.profit = profit

            self.total_profit += profit

            if profit > 0:
                self.winning_trades += 1
            elif profit < 0:
                self.losing_trades += 1

            self.position_manager.close_position(open_position)

            print("End-of-backtest position closed")
            print("Exit Price:", final_price)
            print("Profit:", profit)    
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
        closed_positions = self.position_manager.get_closed_positions()
        open_positions = self.position_manager.get_open_positions()

        profits = [float(position.profit) for position in closed_positions]

        winning_trades = sum(1 for profit in profits if profit > 0)
        losing_trades = sum(1 for profit in profits if profit < 0)

        total_profit = sum(profits)

        closed_trades = len(closed_positions)
        open_trades = len(open_positions)

        if closed_trades > 0:
            win_rate = (winning_trades / closed_trades) * 100
            average_profit = total_profit / closed_trades
        else:
            win_rate = 0.0
            average_profit = 0.0

        gross_profit = sum(profit for profit in profits if profit > 0)
        gross_loss = abs(sum(profit for profit in profits if profit < 0))

        if gross_loss > 0:
            profit_factor = gross_profit / gross_loss
        elif gross_profit > 0:
            profit_factor = float("inf")
        else:
            profit_factor = 0.0

        equity = self.initial_balance
        equity_peak = self.initial_balance
        max_drawdown = 0.0

        for profit in profits:
            equity += profit
            equity_peak = max(equity_peak, equity)
            drawdown = equity_peak - equity
            max_drawdown = max(max_drawdown, drawdown)

        final_equity = self.initial_balance + total_profit

        return BacktestStatistics(
            total_trades=self.total_trades,
            buy_trades=self.buy_trades,
            sell_trades=self.sell_trades,
            winning_trades=winning_trades,
            losing_trades=losing_trades,
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
    def get_closed_positions(self):
        return self.position_manager.get_closed_positions()   