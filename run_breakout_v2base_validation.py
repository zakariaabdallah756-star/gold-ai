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


def print_results(
    title: str,
    results,
) -> None:

    print()
    print(title)
    print("=" * 100)

    if not results:
        print("Nessun trade registrato.")
        print("=" * 100)
        return

    for result in results:
        print(
            f"{result.strategy_name} | "
            f"Regime: {result.market_regime} | "
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

        splitter = HistoricalDataSplitter()

        training_candles, validation_candles = (
            splitter.split(
                candles=candles,
                training_ratio=0.70,
            )
        )

        print(
            "Training candles:",
            len(training_candles),
        )

        print(
            "Validation candles:",
            len(validation_candles),
        )

        runner = StrategyValidationRunner(
            initial_balance=10000.0,
            adaptive_allocation_enabled=False,
            enabled_strategies=[
                "BreakoutStrategyV2Base",
            ],
        )

        (
            training_results,
            validation_results,
        ) = runner.run(
            training_candles=training_candles,
            validation_candles=validation_candles,
        )

        print_results(
            "BREAKOUT V2BASE - TRAINING",
            training_results,
        )

        print_results(
            "BREAKOUT V2BASE - VALIDATION",
            validation_results,
        )

    finally:
        connector.disconnect()


if __name__ == "__main__":
    main()