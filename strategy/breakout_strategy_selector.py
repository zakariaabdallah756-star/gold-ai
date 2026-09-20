from indicators.indicator_values import IndicatorValues


class BreakoutStrategySelector:

    CONSOLIDATION_MAX_WIDTH = 0.01
    STRONG_CANDLE_MIN_BODY_RATIO = 0.50

    def select(
        self,
        indicators: IndicatorValues,
    ) -> str:

        required_values = (
            indicators.current_open,
            indicators.current_high,
            indicators.current_low,
            indicators.current_close,
            indicators.bollinger_upper,
            indicators.bollinger_lower,
        )

        if any(
            value is None
            for value in required_values
        ):
            return "BreakoutStrategy"

        if indicators.current_close <= 0:
            return "BreakoutStrategy"

        bollinger_width = (
            indicators.bollinger_upper
            - indicators.bollinger_lower
        )

        bollinger_width_ratio = (
            bollinger_width
            / indicators.current_close
        )

        consolidation = (
            bollinger_width_ratio
            <= self.CONSOLIDATION_MAX_WIDTH
        )

        if not consolidation:
            return "BreakoutStrategy"

        candle_range = (
            indicators.current_high
            - indicators.current_low
        )

        if candle_range <= 0:
            return "BreakoutStrategyV2Base"

        candle_body = abs(
            indicators.current_close
            - indicators.current_open
        )

        candle_body_ratio = (
            candle_body
            / candle_range
        )

        strong_candle = (
            candle_body_ratio
            >= self.STRONG_CANDLE_MIN_BODY_RATIO
        )

        if strong_candle:
            return "BreakoutStrategyV2"

        return "BreakoutStrategyV2Base"