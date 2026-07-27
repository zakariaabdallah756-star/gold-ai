import MetaTrader5 as mt5

from strategy.signal import SignalType


class MT5MarginChecker:

    def calculate_required_margin(
        self,
        symbol: str,
        signal: SignalType,
        lot_size: float,
        entry_price: float,
    ) -> float:
        if lot_size <= 0 or entry_price <= 0:
            return 0.0

        if signal == SignalType.BUY:
            order_type = mt5.ORDER_TYPE_BUY

        elif signal == SignalType.SELL:
            order_type = mt5.ORDER_TYPE_SELL

        else:
            return 0.0

        margin = mt5.order_calc_margin(
            order_type,
            symbol,
            float(lot_size),
            float(entry_price),
        )

        if margin is None:
            raise RuntimeError(
                "Calcolo margine MT5 fallito: "
                f"{mt5.last_error()}"
            )

        return float(margin)

    def has_sufficient_margin(
        self,
        symbol: str,
        signal: SignalType,
        lot_size: float,
        entry_price: float,
        available_balance: float,
    ) -> bool:
        required_margin = self.calculate_required_margin(
            symbol=symbol,
            signal=signal,
            lot_size=lot_size,
            entry_price=entry_price,
        )

        return required_margin <= available_balance