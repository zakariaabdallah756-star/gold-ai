from pydantic import BaseModel
from datetime import datetime


class Candle(BaseModel):
    time: datetime
    open: float
    high: float
    low: float
    close: float
    volume: int