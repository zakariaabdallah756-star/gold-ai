from contextlib import redirect_stdout
from dataclasses import dataclass
from io import StringIO

from backtest.backtest_engine import BacktestEngine
from market.data_engine import DataEngine


@dataclass
class AllocationComparisonResult:
    mode: str
    total_trades: int
    net_profit: float
    win_rate: float
    profit_factor: float
    final_equity: float
    max_drawdown: float


class AllocationComparison:

    def compare(
        self,
        data_engine: DataEngine,
        initial_balance: float = 10000.0,
    ) -> list[AllocationComparisonResult]:
        results = []

        for mode, enabled in (
            ("STATIC", False),
            ("ADAPTIVE", True),
        ):
            backtest = BacktestEngine(
                data_engine=data_engine,
                initial_balance=initial_balance,
                adaptive_allocation_enabled=enabled,
            )

            # Evita di ristampare tutto il backtest due volte.
            with redirect_stdout(StringIO()):
                backtest.execute()

            statistics = backtest.get_statistics()

            results.append(
                AllocationComparisonResult(
                    mode=mode,
                    total_trades=statistics.total_trades,
                    net_profit=statistics.net_profit,
                    win_rate=statistics.win_rate,
                    profit_factor=statistics.profit_factor,
                    final_equity=statistics.final_equity,
                    max_drawdown=statistics.max_drawdown,
                )
            )

        return results