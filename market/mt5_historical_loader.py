from datetime import datetime, timezone

import MetaTrader5 as mt5

from market.candle import Candle


class MT5HistoricalLoader:

    TIMEFRAMES = {
        "M5": mt5.TIMEFRAME_M5,
        "M15": mt5.TIMEFRAME_M15,
        "M30": mt5.TIMEFRAME_M30,
        "H1": mt5.TIMEFRAME_H1,
        "H4": mt5.TIMEFRAME_H4,
        "D1": mt5.TIMEFRAME_D1,
    }

    def __init__(
        self,
        symbol: str = "XAUUSD",
        timeframe: str = "M15",
    ):
        normalized_timeframe = timeframe.upper()

        if normalized_timeframe not in self.TIMEFRAMES:
            raise ValueError(
                f"Timeframe non supportato: {timeframe}"
            )

        self.symbol = symbol
        self.timeframe_name = normalized_timeframe
        self.timeframe = self.TIMEFRAMES[
            normalized_timeframe
        ]

    def load(
        self,
        count: int = 5000,
        start_pos: int = 1,
    ) -> list[Candle]:
        if count <= 0:
            raise ValueError(
                "count deve essere maggiore di zero."
            )

        if start_pos < 0:
            raise ValueError(
                "start_pos non può essere negativo."
            )

        if not mt5.symbol_select(
            self.symbol,
            True,
        ):
            raise RuntimeError(
                f"Impossibile selezionare "
                f"{self.symbol}: {mt5.last_error()}"
            )

        rates = mt5.copy_rates_from_pos(
            self.symbol,
            self.timeframe,
            start_pos,
            count,
        )

        if rates is None:
            raise RuntimeError(
                "Download storico MT5 fallito: "
                f"{mt5.last_error()}"
            )

        candles = []

        for rate in rates:
            candles.append(
                Candle(
                    time=datetime.fromtimestamp(
                        int(rate["time"]),
                        tz=timezone.utc,
                    ),
                    open=float(rate["open"]),
                    high=float(rate["high"]),
                    low=float(rate["low"]),
                    close=float(rate["close"]),
                    volume=float(rate["tick_volume"]),
                    spread_points=float(rate["spread"]),
                )
            )

        candles.sort(
            key=lambda candle: candle.time
        )

        return candles

    def get_timeframe_name(self) -> str:
        return self.timeframe_name