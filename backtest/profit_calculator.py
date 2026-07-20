from strategy.signal import SignalType


class BacktestProfitCalculator:

    def calculate(
        self,
        signal: SignalType,
        entry_price: float,
        exit_price: float,
        lot_size: float,
    ) -> float:

        if signal == SignalType.BUY:
            return (exit_price - entry_price) * lot_size

        if signal == SignalType.SELL:
            return (entry_price - exit_price) * lot_size

        return 0.0