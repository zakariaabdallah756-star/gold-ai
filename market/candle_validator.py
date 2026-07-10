from market.candle import Candle


class CandleValidator:

    def validate(self, candle: Candle) -> bool:
        if candle is None:
            return False

        if candle.high < candle.low:
            return False

        if candle.open <= 0:
            return False

        if candle.close <= 0:
            return False

        if candle.volume < 0:
            return False

        return True