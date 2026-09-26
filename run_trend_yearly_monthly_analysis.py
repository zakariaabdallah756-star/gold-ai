from collections import defaultdict
from datetime import timedelta

from backtest.strategy_benchmark_runner import (
    StrategyBenchmarkRunner,
)
from market.mt5_connector import MT5Connector
from market.mt5_historical_loader import (
    MT5HistoricalLoader,
)


TIMEFRAME = "M15"
STRATEGY_NAME = "TrendFollowingStrategyV3"
WARMUP_DAYS = 30
MONTHS = 12


def shift_month(
    year,
    month,
    months_delta,
):
    total_months = (
        year * 12
        + (month - 1)
        + months_delta
    )

    new_year = total_months // 12
    new_month = total_months % 12 + 1

    return new_year, new_month


def split_by_month(candles):
    monthly_candles = defaultdict(list)

    for candle in candles:
        key = (
            candle.time.year,
            candle.time.month,
        )

        monthly_candles[key].append(
            candle
        )

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
        loader = MT5HistoricalLoader(
            symbol="XAUUSD",
            timeframe=TIMEFRAME,
        )

        last_complete = loader.load(
            count=1,
            start_pos=1,
        )

        if not last_complete:
            print(
                "Impossibile determinare "
                "l'ultima candela completa."
            )
            return

        latest_time = (
            last_complete[-1].time
        )

        current_month_start = (
            latest_time.replace(
                day=1,
                hour=0,
                minute=0,
                second=0,
                microsecond=0,
            )
        )

        (
            start_year,
            start_month,
        ) = shift_month(
            current_month_start.year,
            current_month_start.month,
            -MONTHS,
        )

        start_time = (
            current_month_start.replace(
                year=start_year,
                month=start_month,
            )
        )

        end_time = (
            current_month_start
            - timedelta(seconds=1)
        )

        candles = loader.load_range(
            start_time=start_time,
            end_time=end_time,
        )

        if not candles:
            print(
                "Nessuna candela disponibile."
            )
            return

        monthly_candles = split_by_month(
            candles
        )

        runner = StrategyBenchmarkRunner(
            initial_balance=10000.0,
            adaptive_allocation_enabled=False,
        )

        print()
        print(
            "TREND V3 M15 - "
            "12 MONTH MONTHLY ANALYSIS"
        )
        print("Start:", start_time)
        print("End:  ", end_time)
        print(
            "Warm-up:",
            WARMUP_DAYS,
            "giorni",
        )

        positive_months = 0
        negative_months = 0
        total_net = 0.0
        total_trades = 0

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

            result = runner.run_strategy(
                strategy_name=(
                    STRATEGY_NAME
                ),
                candles=test_candles,
                trading_start_time=(
                    trading_start_time
                ),
            )

            total_net += (
                result.net_profit
            )

            total_trades += (
                result.total_trades
            )

            if result.net_profit > 0:
                positive_months += 1

            elif result.net_profit < 0:
                negative_months += 1

            print()
            print(
                f"{year}-{month:02d}"
            )

            print(
                f"Warm-up: "
                f"{len(warmup_candles)} | "
                f"Candele: "
                f"{len(month_candles)}"
            )

            print("=" * 100)

            print(
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

        print()
        print("RIEPILOGO 12 MESI")
        print("=" * 100)

        print(
            "Mesi positivi:",
            positive_months,
        )

        print(
            "Mesi negativi:",
            negative_months,
        )

        print(
            "Trades totali:",
            total_trades,
        )

        print(
            f"Net somma mensile: "
            f"{total_net:.2f}"
        )

        print("=" * 100)

    finally:
        connector.disconnect()


if __name__ == "__main__":
    main()