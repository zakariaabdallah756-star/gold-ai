from dataclasses import dataclass

from strategy.signal import SignalType


@dataclass
class BacktestPosition:
    symbol: str
    signal: SignalType
    entry_price: float
    lot_size: float
    is_open: bool = True