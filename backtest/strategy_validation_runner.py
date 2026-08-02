from dataclasses import dataclass

from backtest.backtest_engine import BacktestEngine
from market.candle import Candle
from market.data_engine import DataEngine


@dataclass(frozen=True)
class StrategyPeriodPerformance:
    period_name: str
    strategy_name: str
    market_regime: str
    total_trades: int
    winning_trades: int
    losing_trades: int
    net_profit: float
    win_rate: float
    profit_factor: float


class StrategyValidationRunner:

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

    def run_period(
        self,
        period_name: str,
        candles: list[Candle],
    ) -> list[StrategyPeriodPerformance]:
        if not candles:
            raise ValueError(
                f"Il periodo {period_name} "
                "non contiene candele."
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
        )

        backtest.execute()

        strategy_results = (
            backtest.get_strategy_performance()
        )

        return [
            StrategyPeriodPerformance(
                period_name=period_name,
                strategy_name=result.strategy_name,
                market_regime=result.market_regime,
                total_trades=result.total_trades,
                winning_trades=result.winning_trades,
                losing_trades=result.losing_trades,
                net_profit=result.net_profit,
                win_rate=result.win_rate,
                profit_factor=result.profit_factor,
            )
            for result in strategy_results
        ]

    def run(
        self,
        training_candles: list[Candle],
        validation_candles: list[Candle],
    ) -> tuple[
        list[StrategyPeriodPerformance],
        list[StrategyPeriodPerformance],
    ]:
        training_results = self.run_period(
            period_name="TRAINING",
            candles=training_candles,
        )

        validation_results = self.run_period(
            period_name="VALIDATION",
            candles=validation_candles,
        )

        return training_results, validation_results