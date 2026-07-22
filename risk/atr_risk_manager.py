class ATRRiskManager:

    def calculate_levels(
        self,
        entry_price: float,
        atr: float,
        signal,
        stop_multiplier: float = 1.5,
        target_multiplier: float = 3.0,
    ):
        stop_distance = atr * stop_multiplier
        target_distance = atr * target_multiplier

        if signal.value == "BUY":
            stop_loss = entry_price - stop_distance
            take_profit = entry_price + target_distance

        elif signal.value == "SELL":
            stop_loss = entry_price + stop_distance
            take_profit = entry_price - target_distance

        else:
            return None, None

        return float(stop_loss), float(take_profit)