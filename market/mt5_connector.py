import MetaTrader5 as mt5


class MT5Connector:

    def connect(self):

        if mt5.initialize():
            return True

        print(mt5.last_error())
        return False

    def disconnect(self):
        mt5.shutdown()
    def get_rates(self, symbol="XAUUSD", timeframe=mt5.TIMEFRAME_M15, count=300):

        rates = mt5.copy_rates_from_pos(
            symbol,
            timeframe,
            0,
            count,
        )

        if rates is None:
            return []

        return rates