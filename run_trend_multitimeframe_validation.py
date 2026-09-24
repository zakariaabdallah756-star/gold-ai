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


VALIDATION_PLAN = {
    "M15": [
        "TrendFollowingStrategyV3",
    ],
    "H1": [
        "TrendFollowingStrategy",
        "TrendFollowingStrategyV2",
        "TrendFollowingStrategyV3",
    ],
}


def print_results(
    timeframe: str,
    strategy_name: str,
    period_name: str,
    results,
) -> None:

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

    for result in results:

        if result.strategy_name != strategy_name:
            continue

        print(
            f"Trades: {result.total_trades} | "
            f"Wins: {result.winning_trades} | "
            f"Losses: {result.losing_trades} | "
            f"Net: {result.net_profit:.2f} | "
            f"WR: {result.win_rate:.2f}% | "
            f"PF: {result.profit_factor:.4f}"
        )

    print("=" * 100)


def main():

    connector = MT5Connector()

    if not connector.connect():
        print(
            "Impossibile connettersi a MetaTrader 5."
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

        start_time = start_candidates[0].time

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

        splitter = HistoricalDataSplitter()

        for (
            timeframe,
            strategy_names,
        ) in VALIDATION_PLAN.items():

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
                    f"Nessuna candela per {timeframe}."
                )
                continue

            (
                training_candles,
                validation_candles,
            ) = splitter.split(
                candles=candles,
                training_ratio=0.70,
            )

            print()
            print(
                f"{timeframe} | "
                f"Candele totali: {len(candles)} | "
                f"Training: {len(training_candles)} | "
                f"Validation: {len(validation_candles)}"
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
                        training_candles
                    ),
                    validation_candles=(
                        validation_candles
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