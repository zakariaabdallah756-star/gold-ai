from backtest.strategy_benchmark_runner import (
    StrategyBenchmarkRunner,
)
from market.mt5_historical_loader import MT5HistoricalLoader
from market.mt5_connector import MT5Connector
from strategy.breakout_strategy_selector import (
    BreakoutStrategySelector,
)


STRATEGY_NAMES = [
    "GoldStrategy",
    "TrendFollowingStrategy",
    "BreakoutStrategy",
    "BreakoutStrategyV2Base",
    "BreakoutStrategyV2",
    "MeanReversionStrategy",
    "ScalpingStrategy",
]


def main():

    connector = MT5Connector()

    if not connector.connect():
        print(
            "Impossibile connettersi a MetaTrader 5."
        )
        return

    try:
        loader = MT5HistoricalLoader(
            symbol="XAUUSD",
            timeframe="M15",
        )

        candles = loader.load(
            count=5000,
            start_pos=1,
        )

        print(
            "Candele caricate:",
            len(candles),
        )

        if not candles:
            print(
                "Nessuna candela disponibile."
            )
            return

        runner = StrategyBenchmarkRunner(
            initial_balance=10000.0,
            adaptive_allocation_enabled=False,
        )

        results = runner.run(
            candles=candles,
            strategy_names=STRATEGY_NAMES,
        )

        BreakoutStrategySelector.reset_selection_counts()

        dynamic_breakout_result = (
            runner.run_strategy_group(
                strategy_name="BreakoutSelectorDynamic",
                enabled_strategies=[
                    "BreakoutStrategy",
                    "BreakoutStrategyV2Base",
                    "BreakoutStrategyV2",
                ],
                candles=candles,
            )
        )

        results.append(
            dynamic_breakout_result
        )

        dynamic_strategy_performance = (
            runner.get_last_strategy_performance()
        )

        breakout_selection_counts = (
            BreakoutStrategySelector.get_selection_counts()
        )

        print()
        print("STRATEGY BENCHMARK")
        print("=" * 100)

        for result in results:

            print(
                f"{result.strategy_name} | "
                f"Trades: {result.total_trades} | "
                f"Wins: {result.winning_trades} | "
                f"Losses: {result.losing_trades} | "
                f"Net: {result.net_profit:.2f} | "
                f"WR: {result.win_rate:.2f}% | "
                f"PF: {result.profit_factor:.4f} | "
                f"Equity: {result.final_equity:.2f} | "
                f"DD: {result.max_drawdown:.2f}"
            )

        print("=" * 100)

        print()
        print("BREAKOUT SELECTOR DIAGNOSTIC")
        print("=" * 60)
        print()
        print("DYNAMIC BREAKOUT PERFORMANCE")
        print("=" * 100)

        breakout_strategy_names = {
            "BreakoutStrategy",
            "BreakoutStrategyV2Base",
            "BreakoutStrategyV2",
        }

        for performance in dynamic_strategy_performance:

            if (
                performance.strategy_name
                not in breakout_strategy_names
            ):
                continue

            print(
                f"{performance.strategy_name} | "
                f"Regime: {performance.market_regime} | "
                f"Trades: {performance.total_trades} | "
                f"Wins: {performance.winning_trades} | "
                f"Losses: {performance.losing_trades} | "
                f"Net: {performance.net_profit:.2f} | "
                f"WR: {performance.win_rate:.2f}% | "
                f"PF: {performance.profit_factor:.4f}"
            )

        print("=" * 100)

        for (
            strategy_name,
            count,
        ) in breakout_selection_counts.items():

            print(
                f"{strategy_name}: {count}"
            )

        print("=" * 60)

    finally:
        connector.disconnect()


if __name__ == "__main__":
    main()