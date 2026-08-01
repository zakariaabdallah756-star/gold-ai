from market.mt5_connector import MT5Connector
from market.mt5_historical_loader import (
    MT5HistoricalLoader,
)


def main():
    connector = MT5Connector()

    try:
        connector.connect()

        loader = MT5HistoricalLoader(
            symbol="XAUUSD",
            timeframe="M15",
        )

        candles = loader.load(
            count=5000,
            start_pos=1,
        )

        print()
        print("=" * 60)
        print("Timeframe:", loader.get_timeframe_name())
        print("Candles:", len(candles))

        if candles:
            print("First:", candles[0])
            print("Last:", candles[-1])

        print("=" * 60)

    finally:
        connector.disconnect()


if __name__ == "__main__":
    main()