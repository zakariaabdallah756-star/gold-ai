from data.fake_data import get_fake_candle


class MarketStream:
    def get_next_candle(self):
        return get_fake_candle()