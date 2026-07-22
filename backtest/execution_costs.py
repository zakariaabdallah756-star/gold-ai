import MetaTrader5 as mt5

from strategy.signal import SignalType


class BacktestExecutionCosts:

    def __init__(
        self,
        symbol: str = "XAUUSD",
        commission_per_lot_round_turn: float = 0.0,
        slippage_points: float = 0.0,
    ):
        if commission_per_lot_round_turn < 0:
            raise ValueError(
                "La commissione non può essere negativa."
            )

        if slippage_points < 0:
            raise ValueError(
                "Lo slippage non può essere negativo."
            )

        self.symbol = symbol
        self.commission_per_lot_round_turn = (
            commission_per_lot_round_turn
        )
        self.slippage_points = slippage_points

    def calculate_commission(self, lot_size: float) -> float:
        if lot_size <= 0:
            return 0.0

        return (
            float(lot_size)
            * self.commission_per_lot_round_turn
        )

    def get_slippage_price(self) -> float:
        symbol_info = mt5.symbol_info(self.symbol)

        if symbol_info is None:
            raise RuntimeError(
                f"Informazioni non disponibili per {self.symbol}"
            )

        point = float(symbol_info.point)

        if point <= 0:
            raise RuntimeError(
                f"Valore point non valido per {self.symbol}"
            )

        return self.slippage_points * point

    def apply_entry_slippage(
        self,
        price: float,
        signal: SignalType,
    ) -> float:
        slippage = self.get_slippage_price()

        if signal == SignalType.BUY:
            return float(price) + slippage

        if signal == SignalType.SELL:
            return float(price) - slippage

        return float(price)

    def apply_exit_slippage(
        self,
        price: float,
        signal: SignalType,
    ) -> float:
        slippage = self.get_slippage_price()

        if signal == SignalType.BUY:
            return float(price) - slippage

        if signal == SignalType.SELL:
            return float(price) + slippage

        return float(price)