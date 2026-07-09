class PositionSizer:

    def calculate(
        self,
        balance: float,
        risk_percent: float,
        stop_loss_pips: float,
        pip_value: float,
    ):
        risk_amount = balance * (risk_percent / 100)

        lot_size = risk_amount / (stop_loss_pips * pip_value)

        return round(lot_size, 2)