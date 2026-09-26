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


HISTORY_DAYS = 365
WARMUP_DAYS = 30


CANDIDATES = {
    "M15": [
        "TrendFollowingStrategyV3",
    ],
    "H1": [
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

    found = False

    for result in results:

        if (
            result.strategy_name
            != strategy_name
        ):
            continue

        found = True

        print(
            f"Trades: {result.total_trades} | "
            f"Wins: {result.winning_trades} | "
            f"Losses: {result.losing_trades} | "
            f"Net: {result.net_profit:.2f} | "
            f"WR: {result.win_rate:.2f}% | "
            f"PF: {result.profit_factor:.4f}"
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
        # H1 è il timeframe più alto tra i candidati.
        # Lo usiamo per determinare la fine comune
        # utilizzando esclusivamente una candela completa.
        reference_loader = MT5HistoricalLoader(
            symbol="XAUUSD",
            timeframe="H1",
        )

        last_complete_candles = (
            reference_loader.load(
                count=1,
                start_pos=1,
            )
        )

        if not last_complete_candles:
            print(
                "Impossibile determinare "
                "l'ultima candela H1 completa."
            )
            return

        last_complete_h1 = (
            last_complete_candles[-1]
        )

        end_time = (
            last_complete_h1.time
            + timedelta(hours=1)
            - timedelta(seconds=1)
        )

        start_time = (
            end_time
            - timedelta(days=HISTORY_DAYS)
        )

        print()
        print("TREND LONG HISTORY VALIDATION")
        print("Start:", start_time)
        print("End:  ", end_time)
        print(
            "Storico:",
            HISTORY_DAYS,
            "giorni",
        )
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
                print()
                print(
                    f"Nessuna candela "
                    f"disponibile per {timeframe}."
                )
                continue

            print()
            print(
                f"{timeframe} | "
                f"Candele caricate: "
                f"{len(candles)}"
            )

            print(
                "Prima candela:",
                candles[0].time,
            )

            print(
                "Ultima candela:",
                candles[-1].time,
            )

            (
                training_candles,
                validation_candles,
            ) = splitter.split(
                candles=candles,
                training_ratio=0.70,
            )

            if (
                not training_candles
                or not validation_candles
            ):
                print(
                    f"Dati insufficienti "
                    f"per {timeframe}."
                )
                continue

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

            print(
                f"Training: "
                f"{len(training_candles)} | "
                f"Validation: "
                f"{len(validation_candles)}"
            )

            print(
                "Training start:",
                training_start_time,
            )

            print(
                "Validation start:",
                validation_start_time,
            )

            print(
                "Training warm-up:",
                training_warmup_count,
            )

            print(
                "Validation warm-up:",
                validation_warmup_count,
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