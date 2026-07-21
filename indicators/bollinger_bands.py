from statistics import mean, pstdev


class BollingerBands:

    def calculate(self, candles, period: int = 20, deviation: float = 2.0):
        if len(candles) < period:
            return None, None, None

        closes = [
            float(candle.close)
            for candle in candles[-period:]
        ]

        middle_band = mean(closes)
        standard_deviation = pstdev(closes)

        upper_band = middle_band + deviation * standard_deviation
        lower_band = middle_band - deviation * standard_deviation

        return (
            float(upper_band),
            float(middle_band),
            float(lower_band),
        )