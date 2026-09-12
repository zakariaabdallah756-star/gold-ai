from backtest.strategy_benchmark_runner import (
    StrategyBenchmarkRunner,
)
from market.mt5_historical_loader import MT5HistoricalLoader
from market.mt5_connector import MT5Connector


STRATEGY_NAMES = [
    "GoldStrategy",
    "TrendFollowingStrategy",
    "BreakoutStrategy",
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

    finally:
        connector.disconnect()


if __name__ == "__main__":
    main()