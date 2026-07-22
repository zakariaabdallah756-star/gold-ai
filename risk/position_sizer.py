class PositionSizer:

    def calculate(
        self,
        balance: float,
        risk_percent: float,
        stop_loss_pips: float,
        pip_value: float,
    ) -> float:
        return self.calculate_from_distance(
            balance=balance,
            risk_percent=risk_percent,
            stop_distance=stop_loss_pips,
            value_per_price_unit=pip_value,
        )

    def calculate_from_distance(
        self,
        balance: float,
        risk_percent: float,
        stop_distance: float,
        value_per_price_unit: float,
    ) -> float:
        if balance <= 0:
            return 0.0

        if risk_percent <= 0:
            return 0.0

        if stop_distance <= 0:
            return 0.0

        if value_per_price_unit <= 0:
            return 0.0

        risk_amount = balance * (risk_percent / 100)

        lot_size = risk_amount / (
            stop_distance * value_per_price_unit
        )

        return round(lot_size, 2)