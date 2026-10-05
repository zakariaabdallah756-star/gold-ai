from collections import defaultdict
from datetime import timedelta

from backtest.mt5_commission_detector import (
    MT5CommissionDetector,
)
from backtest.strategy_benchmark_runner import (
    StrategyBenchmarkRunner,
)
from market.mt5_connector import MT5Connector
from market.mt5_historical_loader import (
    MT5HistoricalLoader,
)


WARMUP_DAYS = 30
REFERENCE_M15_CANDLES = 5000

MONTHLY_PLAN = {
    "M5": [
        "ScalpingStrategy",
        "ScalpingStrategyV2",
    ],
}


def split_by_month(candles):
    monthly = defaultdict(list)

    for candle in candles:
        key = (
            candle.time.year,
            candle.time.month,
        )

        monthly[key].append(
            candle
        )

    return monthly


def get_common_interval():
    h1_loader = MT5HistoricalLoader(
        symbol="XAUUSD",
        timeframe="H1",
    )

    last_h1 = h1_loader.load(
        count=1,
        start_pos=1,
    )

    if not last_h1:
        return None, None

    common_end = (
        last_h1[-1].time
        + timedelta(hours=1)
        - timedelta(seconds=1)
    )

    m15_loader = MT5HistoricalLoader(
        symbol="XAUUSD",
        timeframe="M15",
    )

    reference_candidates = (
        m15_loader.load(
            count=5100,
            start_pos=1,
        )
    )

    reference_candidates = [
        candle
        for candle in reference_candidates
        if (
            candle.time
            + timedelta(minutes=15)
            - timedelta(seconds=1)
        ) <= common_end
    ]

    if (
        len(reference_candidates)
        < REFERENCE_M15_CANDLES
    ):
        return None, None

    reference_candles = (
        reference_candidates[
            -REFERENCE_M15_CANDLES:
        ]
    )

    reference_start = (
        reference_candles[0].time
    )

    common_start = (
        reference_start.replace(
            minute=0,
            second=0,
            microsecond=0,
        )
    )

    if (
        reference_start.minute != 0
        or reference_start.second != 0
        or reference_start.microsecond != 0
    ):
        common_start += timedelta(
            hours=1
        )

    return (
        common_start,
        common_end,
    )


def main():

    connector = MT5Connector()

    if not connector.connect():
        print(
            "Impossibile connettersi "
            "a MetaTrader 5."
        )
        return

    try:
        commission_detector = (
            MT5CommissionDetector(
                symbol="XAUUSD",
                lookback_days=365,
            )
        )

        detected_commission = (
            commission_detector
            .calculate_round_turn_per_lot()
        )

        commission = (
            0.0
            if detected_commission is None
            else float(detected_commission)
        )

        (
            common_start,
            common_end,
        ) = get_common_interval()

        if (
            common_start is None
            or common_end is None
        ):
            print(
                "Impossibile costruire "
                "l'intervallo comune."
            )
            return

        print()
        print(
            "SCALPING MONTHLY BENCHMARK"
        )
        print("=" * 100)
        print("Start:", common_start)
        print("End:  ", common_end)
        print(
            "Warm-up:",
            WARMUP_DAYS,
            "giorni",
        )
        print(
            "Commission MT5:",
            commission,
        )
        print("=" * 100)

        runner = StrategyBenchmarkRunner(
            initial_balance=10000.0,
            adaptive_allocation_enabled=False,
            commission_per_lot_round_turn=(
                commission
            ),
        )

        for (
            timeframe,
            strategies,
        ) in MONTHLY_PLAN.items():

            loader = MT5HistoricalLoader(
                symbol="XAUUSD",
                timeframe=timeframe,
            )

            candles = loader.load_range(
                start_time=common_start,
                end_time=common_end,
            )

            if not candles:
                continue

            monthly = split_by_month(
                candles
            )

            print()
            print(
                f"TIMEFRAME {timeframe}"
            )
            print("=" * 100)

            for (
                year,
                month,
            ), month_candles in sorted(
                monthly.items()
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
                    for candle
                    in warmup_candles
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
                    f"{year}-{month:02d}"
                )

                print(
                    f"Candele: "
                    f"{len(month_candles)} | "
                    f"Warm-up: "
                    f"{len(warmup_candles)}"
                )

                for strategy_name in strategies:

                    result = (
                        runner.run_strategy(
                            strategy_name=(
                                strategy_name
                            ),
                            candles=(
                                test_candles
                            ),
                            trading_start_time=(
                                trading_start_time
                            ),
                        )
                    )

                    print(
                        f"{strategy_name} | "
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