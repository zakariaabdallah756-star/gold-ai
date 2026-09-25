from datetime import timedelta

from backtest.historical_data_splitter import (
    HistoricalDataSplitter,
)
from backtest.strategy_validation_runner import (
    StrategyValidationRunner,
)
from market.mt5_connector import MT5Connector
from market.mt5_historical_loader import (
    MT5HistoricalLoader,
)


WARMUP_DAYS = 30


CANDIDATES = {
    "M15": [
        "TrendFollowingStrategyV3",
    ],
    "M30": [
        "TrendFollowingStrategyV5",
    ],
    "H1": [
        "TrendFollowingStrategyV3",
        "TrendFollowingStrategyV5",
    ],
}


def add_warmup(
    loader,
    candles,
):
    trading_start_time = candles[0].time

    warmup_start_time = (
        trading_start_time
        - timedelta(days=WARMUP_DAYS)
    )

    warmup_candles = loader.load_range(
        start_time=warmup_start_time,
        end_time=trading_start_time,
    )

    warmup_candles = [
        candle
        for candle in warmup_candles
        if candle.time < trading_start_time
    ]

    combined_candles = (
        warmup_candles
        + candles
    )

    combined_candles.sort(
        key=lambda candle: candle.time
    )

    return (
        combined_candles,
        trading_start_time,
        len(warmup_candles),
    )


def print_results(
    timeframe,
    strategy_name,
    period_name,
    results,
):

    print()
    print(
        f"{timeframe} | "
        f"{strategy_name} | "
        f"{period_name}"
    )

    print("=" * 100)

    if not results:
        print("Nessun trade registrato.")
        print("=" * 100)
        return

    found = False

    for result in results:

        if (
            result.strategy_name
            != strategy_name
        ):
            continue

        found = True

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
            f"{result.profit_factor:.4f}"
        )

    if not found:
        print("Nessun trade registrato.")

    print("=" * 100)


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

        reference_candles = (
            reference_loader.load(
                count=5000,
                start_pos=1,
            )
        )

        if not reference_candles:
            print(
                "Nessuna candela M15 "
                "disponibile."
            )
            return

        start_time = (
            reference_candles[0].time
        )

        end_time = (
            reference_candles[-1].time
            + timedelta(minutes=15)
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

        splitter = HistoricalDataSplitter()

        for (
            timeframe,
            strategy_names,
        ) in CANDIDATES.items():

            loader = MT5HistoricalLoader(
                symbol="XAUUSD",
                timeframe=timeframe,
            )

            candles = loader.load_range(
                start_time=start_time,
                end_time=end_time,
            )

            if not candles:
                print(
                    f"Nessuna candela "
                    f"per {timeframe}."
                )
                continue

            (
                training_candles,
                validation_candles,
            ) = splitter.split(
                candles=candles,
                training_ratio=0.70,
            )

            (
                training_with_warmup,
                training_start_time,
                training_warmup_count,
            ) = add_warmup(
                loader=loader,
                candles=training_candles,
            )

            (
                validation_with_warmup,
                validation_start_time,
                validation_warmup_count,
            ) = add_warmup(
                loader=loader,
                candles=validation_candles,
            )

            print()
            print(
                f"{timeframe} | "
                f"Totale: {len(candles)} | "
                f"Training: "
                f"{len(training_candles)} | "
                f"Validation: "
                f"{len(validation_candles)}"
            )

            print(
                f"Training warm-up: "
                f"{training_warmup_count}"
            )

            print(
                f"Validation warm-up: "
                f"{validation_warmup_count}"
            )

            print(
                "Training start:",
                training_start_time,
            )

            print(
                "Validation start:",
                validation_start_time,
            )

            for strategy_name in strategy_names:

                runner = StrategyValidationRunner(
                    initial_balance=10000.0,
                    adaptive_allocation_enabled=False,
                    enabled_strategies=[
                        strategy_name,
                    ],
                )

                (
                    training_results,
                    validation_results,
                ) = runner.run(
                    training_candles=(
                        training_with_warmup
                    ),
                    validation_candles=(
                        validation_with_warmup
                    ),
                    training_start_time=(
                        training_start_time
                    ),
                    validation_start_time=(
                        validation_start_time
                    ),
                )

                print_results(
                    timeframe=timeframe,
                    strategy_name=strategy_name,
                    period_name="TRAINING",
                    results=training_results,
                )

                print_results(
                    timeframe=timeframe,
                    strategy_name=strategy_name,
                    period_name="VALIDATION",
                    results=validation_results,
                )

    finally:
        connector.disconnect()


if __name__ == "__main__":
    main()