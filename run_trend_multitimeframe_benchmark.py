from datetime import timedelta

from backtest.strategy_benchmark_runner import (
    StrategyBenchmarkRunner,
)
from market.mt5_connector import MT5Connector
from market.mt5_historical_loader import (
    MT5HistoricalLoader,
)


TIMEFRAMES = [
    "M15",
    "M30",
    "H1",
    "H4",
]

TREND_STRATEGIES = [
    "TrendFollowingStrategy",
    "TrendFollowingStrategyV2",
    "TrendFollowingStrategyV3",
    "TrendFollowingStrategyV4",
    "TrendFollowingStrategyV5",
]

def main():

    connector = MT5Connector()

    if not connector.connect():
        print(
            "Impossibile connettersi a MetaTrader 5."
        )
        return

    try:
        # Prendiamo circa 5000 M15 come riferimento
        reference_loader = MT5HistoricalLoader(
            symbol="XAUUSD",
            timeframe="M15",
        )

        reference_candles = reference_loader.load(
            count=5000,
            start_pos=1,
        )

        if not reference_candles:
            print("Nessuna candela M15 disponibile.")
            return

        reference_start = reference_candles[0].time

        # Allineiamo l'inizio alla prima H4 completa
        h4_loader = MT5HistoricalLoader(
            symbol="XAUUSD",
            timeframe="H4",
        )

        start_candidates = h4_loader.load_range(
            start_time=reference_start,
            end_time=(
                reference_start
                + timedelta(hours=8)
            ),
        )

        if not start_candidates:
            print(
                "Impossibile determinare "
                "l'inizio H4."
            )
            return

        start_time = start_candidates[0].time

        # Ultima H4 completa
        last_h4_candles = h4_loader.load(
            count=1,
            start_pos=1,
        )

        if not last_h4_candles:
            print(
                "Impossibile determinare "
                "l'ultima H4 completa."
            )
            return

        end_time = (
            last_h4_candles[-1].time
            + timedelta(hours=4)
            - timedelta(seconds=1)
        )

        if start_time >= end_time:
            print(
                "Intervallo storico non valido."
            )
            return

        print()
        print("INTERVALLO COMUNE")
        print("Start:", start_time)
        print("End:  ", end_time)

        runner = StrategyBenchmarkRunner(
            initial_balance=10000.0,
            adaptive_allocation_enabled=False,
        )

        for timeframe in TIMEFRAMES:

            loader = MT5HistoricalLoader(
                symbol="XAUUSD",
                timeframe=timeframe,
            )

            candles = loader.load_range(
                start_time=start_time,
                end_time=end_time,
            )

            print()
            print(
                f"TREND BENCHMARK - {timeframe}"
            )
            print(
                f"Candele: {len(candles)}"
            )
            print("=" * 100)

            if not candles:
                print(
                    "Nessuna candela disponibile."
                )
                print("=" * 100)
                continue

            results = runner.run(
                candles=candles,
                strategy_names=TREND_STRATEGIES,
            )

            for result in results:
                print(
                    f"{result.strategy_name} | "
                    f"Trades: {result.total_trades} | "
                    f"Wins: {result.winning_trades} | "
                    f"Losses: {result.losing_trades} | "
                    f"Net: {result.net_profit:.2f} | "
                    f"WR: {result.win_rate:.2f}% | "
                    f"PF: {result.profit_factor:.4f} | "
                    f"Equity: "
                    f"{result.final_equity:.2f} | "
                    f"DD: "
                    f"{result.max_drawdown:.2f}"
                )

            print("=" * 100)

    finally:
        connector.disconnect()


if __name__ == "__main__":
    main()