import MetaTrader5 as mt5

from market.candle import Candle
from strategy.signal import SignalType


class BacktestSpreadCalculator:

    def __init__(self, symbol: str = "XAUUSD"):
        self.symbol = symbol

    def get_spread_price(self, candle: Candle) -> float:
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

        spread_points = max(
            float(candle.spread_points),
            0.0,
        )

        return spread_points * point

    def get_entry_price(
        self,
        candle: Candle,
        signal: SignalType,
    ) -> float:
        bid_price = float(candle.close)

        if signal == SignalType.BUY:
            return bid_price + self.get_spread_price(candle)

        return bid_price

    def get_market_exit_price(
        self,
        candle: Candle,
        signal: SignalType,
    ) -> float:
        bid_price = float(candle.close)

        if signal == SignalType.SELL:
            return bid_price + self.get_spread_price(candle)

        return bid_price

    def get_exit_range(
        self,
        candle: Candle,
        signal: SignalType,
    ) -> tuple[float, float]:
        low_price = float(candle.low)
        high_price = float(candle.high)

        if signal == SignalType.SELL:
            spread_price = self.get_spread_price(candle)

            low_price += spread_price
            high_price += spread_price

        return low_price, high_price