from pydantic import BaseModel


class RiskModel(BaseModel):
    risk_percent: float
    stop_loss_pips: float
    take_profit_pips: float