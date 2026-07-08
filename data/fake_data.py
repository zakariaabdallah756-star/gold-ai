from datetime import datetime
from data.candle import Candle


def get_fake_candle():
    return Candle(
        time=datetime.now(),
        open=3300.0,
        high=3310.0,
        low=3295.0,
        close=3305.0,
        volume=1200
    )