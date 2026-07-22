from datetime import datetime
from datetime import datetime

from market.candle import Candle


class DataProvider:
    def get_server_time(self):
        return datetime.now()
    def get_mt5_candles(self, connector, count=300):
        rates = connector.get_rates(count=count)

        candles = []

        for rate in rates:
            candles.append(
                Candle(
                    time=datetime.fromtimestamp(int(rate["time"])),
                    open=float(rate["open"]),
                    high=float(rate["high"]),
                    low=float(rate["low"]),
                    close=float(rate["close"]),
                    volume=float(rate["tick_volume"]),
                    spread_points=float(rate["spread"]),
                )
            )

        return candles