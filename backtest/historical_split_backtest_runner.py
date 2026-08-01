from dataclasses import dataclass

from backtest.backtest_engine import BacktestEngine
from market.candle import Candle
from market.data_engine import DataEngine


@dataclass(frozen=True)
class HistoricalPeriodResult:
    period_name: str
    candles: int
    total_trades: int
    net_profit: float
    win_rate: float
    profit_factor: float
    final_equity: float
    max_drawdown: float


class HistoricalSplitBacktestRunner:

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
    ) -> HistoricalPeriodResult:
        if not candles:
            raise ValueError(
                f"Il periodo {period_name} non contiene candele."
            )

        data_engine = self._build_data_engine(candles)

        backtest = BacktestEngine(
            data_engine=data_engine,
            initial_balance=self.initial_balance,
            adaptive_allocation_enabled=(
                self.adaptive_allocation_enabled
            ),
            verbose=False,
        )

        backtest.execute()

        statistics = backtest.get_statistics()

        return HistoricalPeriodResult(
            period_name=period_name,
            candles=len(candles),
            total_trades=statistics.total_trades,
            net_profit=statistics.net_profit,
            win_rate=statistics.win_rate,
            profit_factor=statistics.profit_factor,
            final_equity=statistics.final_equity,
            max_drawdown=statistics.max_drawdown,
        )

    def run(
        self,
        training_candles: list[Candle],
        validation_candles: list[Candle],
    ) -> tuple[
        HistoricalPeriodResult,
        HistoricalPeriodResult,
    ]:
        training_result = self.run_period(
            period_name="TRAINING",
            candles=training_candles,
        )

        validation_result = self.run_period(
            period_name="VALIDATION",
            candles=validation_candles,
        )

        return training_result, validation_result