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


TIMEFRAME = "M5"
STRATEGY_NAME = "ScalpingStrategyV3"

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


def build_month_periods(
    current_month_start,
):
    periods = []

    for months_back in range(
        MONTHS,
        0,
        -1,
    ):
        (
            year,
            month,
        ) = shift_month(
            current_month_start.year,
            current_month_start.month,
            -months_back,
        )

        (
            next_year,
            next_month,
        ) = shift_month(
            year,
            month,
            1,
        )

        month_start = (
            current_month_start.replace(
                year=year,
                month=month,
            )
        )

        next_month_start = (
            current_month_start.replace(
                year=next_year,
                month=next_month,
            )
        )

        month_end = (
            next_month_start
            - timedelta(seconds=1)
        )

        periods.append(
            (
                year,
                month,
                month_start,
                month_end,
            )
        )

    return periods


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

        print(
            "Commission MT5 round turn per lot:",
            commission,
        )

        print(
            "Timeframe:",
            TIMEFRAME,
        )

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

        month_periods = (
            build_month_periods(
                current_month_start
            )
        )

        if not month_periods:
            print(
                "Impossibile costruire "
                "i periodi mensili."
            )
            return

        start_time = (
            month_periods[0][2]
        )

        end_time = (
            month_periods[-1][3]
        )

        runner = StrategyBenchmarkRunner(
            initial_balance=10000.0,
            adaptive_allocation_enabled=False,
            commission_per_lot_round_turn=(
                commission
            ),
        )

        print()
        print(
            f"SCALPING V3 {TIMEFRAME} - "
            f"{MONTHS} MONTH MONTHLY ANALYSIS"
        )

        print(
            "Start:",
            start_time,
        )

        print(
            "End:  ",
            end_time,
        )

        print(
            "Warm-up:",
            WARMUP_DAYS,
            "giorni",
        )

        print(
            "Download storico: mese per mese"
        )

        positive_months = 0
        negative_months = 0
        neutral_months = 0

        total_net = 0.0
        total_trades = 0
        total_wins = 0
        total_losses = 0

        for (
            year,
            month,
            month_start,
            month_end,
        ) in month_periods:

            print()
            print(
                f"Download "
                f"{year}-{month:02d}..."
            )

            try:
                month_candles = (
                    loader.load_range(
                        start_time=month_start,
                        end_time=month_end,
                    )
                )

            except RuntimeError as error:
                print(
                    f"Errore download "
                    f"{year}-{month:02d}:"
                )
                print(error)
                return

            if not month_candles:
                print(
                    f"Nessuna candela "
                    f"disponibile per "
                    f"{year}-{month:02d}."
                )
                return

            trading_start_time = (
                month_candles[0].time
            )

            warmup_start_time = (
                trading_start_time
                - timedelta(
                    days=WARMUP_DAYS
                )
            )

            try:
                warmup_candles = (
                    loader.load_range(
                        start_time=warmup_start_time,
                        end_time=trading_start_time,
                    )
                )

            except RuntimeError as error:
                print(
                    f"Errore download warm-up "
                    f"{year}-{month:02d}:"
                )
                print(error)
                return

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

            result = (
                runner.run_strategy(
                    strategy_name=(
                        STRATEGY_NAME
                    ),
                    candles=(
                        test_candles
                    ),
                    trading_start_time=(
                        trading_start_time
                    ),
                )
            )

            total_net += (
                result.net_profit
            )

            total_trades += (
                result.total_trades
            )

            total_wins += (
                result.winning_trades
            )

            total_losses += (
                result.losing_trades
            )

            if result.net_profit > 0:
                positive_months += 1

            elif result.net_profit < 0:
                negative_months += 1

            else:
                neutral_months += 1

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

            print(
                "Trading start:",
                trading_start_time,
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

        total_win_rate = (
            (
                total_wins
                / total_trades
            )
            * 100.0
            if total_trades > 0
            else 0.0
        )

        print()
        print(
            f"RIEPILOGO {MONTHS} MESI"
        )

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
            "Mesi neutri:",
            neutral_months,
        )

        print(
            "Trades totali:",
            total_trades,
        )

        print(
            "Wins totali:",
            total_wins,
        )

        print(
            "Losses totali:",
            total_losses,
        )

        print(
            f"Win Rate totale: "
            f"{total_win_rate:.2f}%"
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