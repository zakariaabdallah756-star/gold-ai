from enum import Enum
from pydantic import BaseModel


class SignalType(str, Enum):
    BUY = "BUY"
    SELL = "SELL"
    HOLD = "HOLD"


class Signal(BaseModel):
    signal: SignalType
    confidence: float