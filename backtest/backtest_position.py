from dataclasses import dataclass

from strategy.signal import SignalType


@dataclass
class BacktestPosition:
    symbol: str
    signal: SignalType
    entry_price: float
    lot_size: float
    stop_loss: float
    take_profit: float
    is_open: bool = True
    exit_price: float | None = None
    profit: float = 0.0