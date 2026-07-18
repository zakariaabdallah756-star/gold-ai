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
                    time=datetime.fromtimestamp(rate["time"]),
                    open=rate["open"],
                    high=rate["high"],
                    low=rate["low"],
                    close=rate["close"],
                    volume=rate["tick_volume"],
                )
            )

        return candles