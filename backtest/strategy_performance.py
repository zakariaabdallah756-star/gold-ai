from pydantic import BaseModel


class StrategyPerformance(BaseModel):
    strategy_name: str
    market_regime: str
    total_trades: int
    winning_trades: int
    losing_trades: int
    net_profit: float
    win_rate: float
    profit_factor: float