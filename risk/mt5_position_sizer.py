from decimal import Decimal, ROUND_DOWN

import MetaTrader5 as mt5

from strategy.signal import SignalType


class MT5PositionSizer:

    def calculate(
        self,
        symbol: str,
        signal: SignalType,
        balance: float,
        risk_percent: float,
        entry_price: float,
        stop_loss: float,
    ) -> float:
        if balance <= 0 or risk_percent <= 0:
            return 0.0

        if entry_price <= 0 or stop_loss <= 0:
            return 0.0

        if not mt5.symbol_select(symbol, True):
            raise RuntimeError(
                f"Impossibile selezionare {symbol}: {mt5.last_error()}"
            )

        symbol_info = mt5.symbol_info(symbol)

        if symbol_info is None:
            raise RuntimeError(
                f"Informazioni non disponibili per {symbol}"
            )

        if signal == SignalType.BUY:
            order_type = mt5.ORDER_TYPE_BUY
        elif signal == SignalType.SELL:
            order_type = mt5.ORDER_TYPE_SELL
        else:
            return 0.0

        loss_for_one_lot = mt5.order_calc_profit(
            order_type,
            symbol,
            1.0,
            entry_price,
            stop_loss,
        )

        if loss_for_one_lot is None:
            raise RuntimeError(
                f"Calcolo rischio MT5 fallito: {mt5.last_error()}"
            )

        loss_per_lot = abs(float(loss_for_one_lot))

        if loss_per_lot <= 0:
            return 0.0

        risk_amount = balance * (risk_percent / 100)
        raw_lot = risk_amount / loss_per_lot

        minimum = Decimal(str(symbol_info.volume_min))
        maximum = Decimal(str(symbol_info.volume_max))
        step = Decimal(str(symbol_info.volume_step))
        raw = Decimal(str(raw_lot))

        normalized = (
            raw / step
        ).to_integral_value(rounding=ROUND_DOWN) * step

        # Evita di superare il rischio usando il lotto minimo.
        if normalized < minimum:
            return 0.0

        if normalized > maximum:
            normalized = maximum

        return float(normalized)