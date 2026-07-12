class BacktestProfitCalculator:

    def calculate(self, signal):
        if signal.signal.value == "BUY":
            return 10.0

        if signal.signal.value == "SELL":
            return -10.0

        return 0.0