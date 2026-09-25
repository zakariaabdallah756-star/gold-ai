from collections import defaultdict
from datetime import timedelta

from backtest.strategy_benchmark_runner import (
    StrategyBenchmarkRunner,
)
from market.mt5_connector import MT5Connector
from market.mt5_historical_loader import (
    MT5HistoricalLoader,
)


MONTHLY_PLAN = {
    "M15": [
        "TrendFollowingStrategyV3",
    ],
    "H1": [
        "TrendFollowingStrategy",
        "TrendFollowingStrategyV2",
        "TrendFollowingStrategyV3",
        "TrendFollowingStrategyV4",
    ],
}


WARMUP_DAYS = 30


def split_by_month(candles):

    monthly_candles = defaultdict(list)

    for candle in candles:

        month_key = (
            candle.time.year,
            candle.time.month,
        )

        monthly_candles[
            month_key
        ].append(candle)

    return monthly_candles


def main():

    connector = MT5Connector()

    if not connector.connect():
        print(
            "Impossibile connettersi "
            "a MetaTrader 5."
        )
        return

    try:
        reference_loader = MT5HistoricalLoader(
            symbol="XAUUSD",
            timeframe="M15",
        )

        reference_candles = reference_loader.load(
            count=5000,
            start_pos=1,
        )

        if not reference_candles:
            print(
                "Nessuna candela M15 disponibile."
            )
            return

        reference_start = (
            reference_candles[0].time
        )

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
                "l'inizio dell'intervallo."
            )
            return

        start_time = (
            start_candidates[0].time
        )

        last_h4_candles = h4_loader.load(
            count=1,
            start_pos=1,
        )

        if not last_h4_candles:
            print(
                "Impossibile determinare "
                "la fine dell'intervallo."
            )
            return

        end_time = (
            last_h4_candles[-1].time
            + timedelta(hours=4)
            - timedelta(seconds=1)
        )

        print()
        print("INTERVALLO COMUNE")
        print("Start:", start_time)
        print("End:  ", end_time)
        print(
            "Warm-up:",
            WARMUP_DAYS,
            "giorni",
        )

        runner = StrategyBenchmarkRunner(
            initial_balance=10000.0,
            adaptive_allocation_enabled=False,
        )

        for (
            timeframe,
            strategy_names,
        ) in MONTHLY_PLAN.items():

            loader = MT5HistoricalLoader(
                symbol="XAUUSD",
                timeframe=timeframe,
            )

            candles = loader.load_range(
                start_time=start_time,
                end_time=end_time,
            )

            monthly_candles = split_by_month(
                candles
            )

            for (
                year,
                month,
            ), month_candles in sorted(
                monthly_candles.items()
            ):

                if not month_candles:
                    continue

                trading_start_time = (
                    month_candles[0].time
                )

                warmup_start_time = (
                    trading_start_time
                    - timedelta(
                        days=WARMUP_DAYS
                    )
                )

                warmup_candles = (
                    loader.load_range(
                        start_time=(
                            warmup_start_time
                        ),
                        end_time=(
                            trading_start_time
                        ),
                    )
                )

                warmup_candles = [
                    candle
                    for candle in warmup_candles
                    if (
                        candle.time
                        < trading_start_time
                    )
                ]

                test_candles = (
                    warmup_candles
                    + month_candles
                )

                test_candles.sort(
                    key=lambda candle: candle.time
                )

                print()
                print(
                    f"{timeframe} | "
                    f"{year}-{month:02d}"
                )

                print(
                    f"Warm-up: "
                    f"{len(warmup_candles)} | "
                    f"Candele mese: "
                    f"{len(month_candles)}"
                )

                print(
                    "Trading start:",
                    trading_start_time,
                )

                print("=" * 100)

                for strategy_name in strategy_names:

                    result = runner.run_strategy(
                        strategy_name=(
                            strategy_name
                        ),
                        candles=test_candles,
                        trading_start_time=(
                            trading_start_time
                        ),
                    )

                    print(
                        f"{result.strategy_name} | "
                        f"Trades: "
                        f"{result.total_trades} | "
                        f"Wins: "
                        f"{result.winning_trades} | "
                        f"Losses: "
                        f"{result.losing_trades} | "
                        f"Net: "
                        f"{result.net_profit:.2f} | "
                        f"WR: "
                        f"{result.win_rate:.2f}% | "
                        f"PF: "
                        f"{result.profit_factor:.4f} | "
                        f"DD: "
                        f"{result.max_drawdown:.2f}"
                    )

                print("=" * 100)

    finally:
        connector.disconnect()


if __name__ == "__main__":
    main()