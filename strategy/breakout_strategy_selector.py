from indicators.indicator_values import IndicatorValues


class BreakoutStrategySelector:

    CONSOLIDATION_MAX_WIDTH = 0.01
    STRONG_CANDLE_MIN_BODY_RATIO = 0.50

    _selection_counts = {
        "BreakoutStrategy": 0,
        "BreakoutStrategyV2Base": 0,
        "BreakoutStrategyV2": 0,
        "HOLD": 0,
    }

    @classmethod
    def reset_selection_counts(cls) -> None:
        for strategy_name in cls._selection_counts:
            cls._selection_counts[strategy_name] = 0

    @classmethod
    def get_selection_counts(cls) -> dict[str, int]:
        return cls._selection_counts.copy()

    def _record(self, strategy_name: str) -> str:
        self.__class__._selection_counts[strategy_name] += 1
        return strategy_name

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
            return self._record(
                "BreakoutStrategy"
            )

        if indicators.current_close <= 0:
            return self._record(
                "BreakoutStrategy"
            )

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
            return self._record(
                "HOLD"
            )

        candle_range = (
            indicators.current_high
            - indicators.current_low
        )

        if candle_range <= 0:
            return self._record(
                "BreakoutStrategyV2Base"
            )

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
            return self._record(
                "BreakoutStrategyV2"
            )

        return self._record(
            "BreakoutStrategyV2Base"
        )