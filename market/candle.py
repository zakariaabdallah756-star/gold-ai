from dataclasses import dataclass
from datetime import datetime


@dataclass
class Candle:
    time: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float
    spread_points: float = 0.0