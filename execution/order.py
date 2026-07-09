from pydantic import BaseModel
from strategy.signal import SignalType


class Order(BaseModel):
    symbol: str
    signal: SignalType
    lot_size: float
    stop_loss_pips: float
    take_profit_pips: float