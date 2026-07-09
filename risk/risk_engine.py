from risk.position_sizer import PositionSizer


class RiskEngine:
    def __init__(self):
        self.position_sizer = PositionSizer()

    def calculate_position_size(
        self,
        balance: float,
        risk_percent: float,
        stop_loss_pips: float,
        pip_value: float,
    ):
        return self.position_sizer.calculate(
            balance=balance,
            risk_percent=risk_percent,
            stop_loss_pips=stop_loss_pips,
            pip_value=pip_value,
        )