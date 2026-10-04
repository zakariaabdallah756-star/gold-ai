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


TIMEFRAMES = (
    "M5",
    "M15",
    "M30",
    "H1",
)

STRATEGIES = (
    "MeanReversionStrategy",
    "MeanReversionStrategyV2",
    "MeanReversionStrategyV3",
)

WARMUP_DAYS = 30
REFERENCE_M15_CANDLES = 5000


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

    combined = (
        warmup_candles
        + candles
    )

    combined.sort(
        key=lambda candle: candle.time
    )

    return (
        combined,
        trading_start_time,
        len(warmup_candles),
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

        # Usiamo l'ultima H1 completa
        # per evitare barre parziali.
        h1_loader = MT5HistoricalLoader(
            symbol="XAUUSD",
            timeframe="H1",
        )

        last_h1 = h1_loader.load(
            count=1,
            start_pos=1,
        )

        if not last_h1:
            print(
                "Impossibile determinare "
                "l'ultima H1 completa."
            )
            return

        common_end = (
            last_h1[-1].time
            + timedelta(hours=1)
            - timedelta(seconds=1)
        )

        # Recuperiamo qualche barra M15 in più,
        # poi prendiamo esattamente le ultime
        # 5000 completamente chiuse entro common_end.
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
            print(
                "Storico M15 insufficiente "
                "per costruire l'intervallo."
            )
            return

        reference_candles = (
            reference_candidates[
                -REFERENCE_M15_CANDLES:
            ]
        )

        reference_start = (
            reference_candles[0].time
        )

        # Allineamento all'ora intera:
        # in questo modo tutti i timeframe
        # iniziano sullo stesso confine temporale.
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
            common_start += timedelta(hours=1)

        print()
        print(
            "MEAN REVERSION MULTITIMEFRAME BENCHMARK"
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
            "Commission MT5 round turn per lot:",
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

        for timeframe in TIMEFRAMES:

            loader = MT5HistoricalLoader(
                symbol="XAUUSD",
                timeframe=timeframe,
            )

            candles = loader.load_range(
                start_time=common_start,
                end_time=common_end,
            )

            if not candles:
                print()
                print(
                    f"{timeframe}: "
                    "nessuna candela disponibile."
                )
                continue

            (
                candles_with_warmup,
                trading_start_time,
                warmup_count,
            ) = add_warmup(
                loader=loader,
                candles=candles,
            )

            print()
            print(
                f"TIMEFRAME {timeframe}"
            )
            print("=" * 100)

            print(
                "Candele operative:",
                len(candles),
            )

            print(
                "Warm-up candles:",
                warmup_count,
            )

            print(
                "Trading start:",
                trading_start_time,
            )

            for strategy_name in STRATEGIES:

                result = runner.run_strategy(
                    strategy_name=(
                        strategy_name
                    ),
                    candles=(
                        candles_with_warmup
                    ),
                    trading_start_time=(
                        trading_start_time
                    ),
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