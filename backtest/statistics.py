from pydantic import BaseModel


class BacktestStatistics(BaseModel):
    total_trades: int
    buy_trades: int
    sell_trades: int
    winning_trades: int
    losing_trades: int
    total_profit: float
    win_rate: float
    open_trades: int
    closed_trades: int