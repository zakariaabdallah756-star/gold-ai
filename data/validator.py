from data.candle import Candle


class CandleValidator:
    def validate(self, candle: Candle) -> bool:
        return (
            candle.high >= candle.low
            and candle.high >= candle.open
            and candle.high >= candle.close
            and candle.low <= candle.open
            and candle.low <= candle.close
            and candle.volume >= 0
        )