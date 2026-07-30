class AdaptiveStrategyAllocator:

    def __init__(
        self,
        minimum_multiplier: float = 0.50,
        maximum_multiplier: float = 1.50,
        minimum_trades: int = 3,
    ):
        if minimum_multiplier <= 0:
            raise ValueError(
                "minimum_multiplier deve essere maggiore di zero."
            )

        if maximum_multiplier < minimum_multiplier:
            raise ValueError(
                "maximum_multiplier non può essere inferiore "
                "a minimum_multiplier."
            )

        if minimum_trades <= 0:
            raise ValueError(
                "minimum_trades deve essere maggiore di zero."
            )

        self.minimum_multiplier = minimum_multiplier
        self.maximum_multiplier = maximum_multiplier
        self.minimum_trades = minimum_trades

    def calculate_multiplier(
        self,
        score: float,
        trade_count: int,
    ) -> float:
        if trade_count < self.minimum_trades:
            return 1.0

        return max(
            self.minimum_multiplier,
            min(
                float(score),
                self.maximum_multiplier,
            ),
        )

    def calculate_weight(
        self,
        base_weight: float,
        score: float,
        trade_count: int,
    ) -> float:
        if base_weight <= 0:
            return 0.0

        multiplier = self.calculate_multiplier(
            score=score,
            trade_count=trade_count,
        )

        adaptive_weight = (
            float(base_weight) * multiplier
        )

        return max(
            0.0,
            min(
                adaptive_weight,
                1.0,
            ),
        )