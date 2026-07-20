import pandas as pd
from ta.trend import ADXIndicator


class ADX:

    def calculate(self, candles, period=14):

        if len(candles) < period * 2:
            return None

        highs = pd.Series(
            [float(candle.high) for candle in candles],
            dtype="float64",
        )

        lows = pd.Series(
            [float(candle.low) for candle in candles],
            dtype="float64",
        )

        closes = pd.Series(
            [float(candle.close) for candle in candles],
            dtype="float64",
        )

        indicator = ADXIndicator(
            high=highs,
            low=lows,
            close=closes,
            window=period,
        )

        value = indicator.adx().iloc[-1]

        if pd.isna(value):
            return None

        return float(value)