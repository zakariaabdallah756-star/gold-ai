class BacktestProfitCalculator:
    def calculate(
        self,
        signal,
        entry_price: float,
        exit_price: float,
        lot_size: float,
    ):
        if signal.signal.value == "BUY":
            return (exit_price - entry_price) * lot_size

        if signal.signal.value == "SELL":
            return (entry_price - exit_price) * lot_size

        return 0.0