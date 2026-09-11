from dataclasses import dataclass

from backtest.backtest_engine import BacktestEngine
from market.candle import Candle
from market.data_engine import DataEngine


@dataclass(frozen=True)
class StrategyBenchmarkResult:
    strategy_name: str
    candles: int
    total_trades: int
    winning_trades: int
    losing_trades: int
    net_profit: float
    win_rate: float
    profit_factor: float
    final_equity: float
    max_drawdown: float


class StrategyBenchmarkRunner:

    def __init__(
        self,
        initial_balance: float = 10000.0,
        adaptive_allocation_enabled: bool = False,
    ):
        if initial_balance <= 0:
            raise ValueError(
                "initial_balance deve essere maggiore di zero."
            )

        self.initial_balance = float(initial_balance)

        self.adaptive_allocation_enabled = bool(
            adaptive_allocation_enabled
        )

    def _build_data_engine(
        self,
        candles: list[Candle],
    ) -> DataEngine:

        data_engine = DataEngine()

        for candle in candles:
            data_engine.add_candle(candle)

        return data_engine

    def run_strategy(
        self,
        strategy_name: str,
        candles: list[Candle],
    ) -> StrategyBenchmarkResult:

        if not candles:
            raise ValueError(
                "La lista delle candele è vuota."
            )

        data_engine = self._build_data_engine(
            candles
        )

        backtest = BacktestEngine(
            data_engine=data_engine,
            initial_balance=self.initial_balance,
            adaptive_allocation_enabled=(
                self.adaptive_allocation_enabled
            ),
            verbose=False,
            enabled_strategies=[
                strategy_name,
            ],
        )

        backtest.execute()

        statistics = backtest.get_statistics()

        return StrategyBenchmarkResult(
            strategy_name=strategy_name,
            candles=len(candles),
            total_trades=statistics.total_trades,
            winning_trades=statistics.winning_trades,
            losing_trades=statistics.losing_trades,
            net_profit=statistics.net_profit,
            win_rate=statistics.win_rate,
            profit_factor=statistics.profit_factor,
            final_equity=statistics.final_equity,
            max_drawdown=statistics.max_drawdown,
        )

    def run(
        self,
        candles: list[Candle],
        strategy_names: list[str],
    ) -> list[StrategyBenchmarkResult]:

        if not candles:
            raise ValueError(
                "La lista delle candele è vuota."
            )

        results = []

        for strategy_name in strategy_names:

            result = self.run_strategy(
                strategy_name=strategy_name,
                candles=candles,
            )

            results.append(result)

        return results