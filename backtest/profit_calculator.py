import MetaTrader5 as mt5

from strategy.signal import SignalType


class BacktestProfitCalculator:

    def calculate(
        self,
        signal: SignalType,
        entry_price: float,
        exit_price: float,
        lot_size: float,
        symbol: str = "XAUUSD",
    ) -> float:
        if (
            entry_price <= 0
            or exit_price <= 0
            or lot_size <= 0
        ):
            return 0.0

        if not mt5.symbol_select(symbol, True):
            raise RuntimeError(
                f"Impossibile selezionare {symbol}: "
                f"{mt5.last_error()}"
            )

        if signal == SignalType.BUY:
            order_type = mt5.ORDER_TYPE_BUY

        elif signal == SignalType.SELL:
            order_type = mt5.ORDER_TYPE_SELL

        else:
            return 0.0

        profit = mt5.order_calc_profit(
            order_type,
            symbol,
            float(lot_size),
            float(entry_price),
            float(exit_price),
        )

        if profit is None:
            raise RuntimeError(
                f"Calcolo profitto MT5 fallito: "
                f"{mt5.last_error()}"
            )

        return float(profit)