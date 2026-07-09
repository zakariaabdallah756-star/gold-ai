from pydantic import BaseModel
from strategy.signal import SignalType


class Position(BaseModel):
    symbol: str
    signal: SignalType
    lot_size: float
    entry_price: float
    stop_loss: float
    take_profit: float
    is_open: bool = True