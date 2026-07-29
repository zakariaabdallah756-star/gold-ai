class StrategyPortfolioManager:

    def __init__(self):
        self.enabled_strategies = {
            "GoldStrategy": True,
            "TrendFollowingStrategy": True,
            "BreakoutStrategy": True,
            "MeanReversionStrategy": True,
            "ScalpingStrategy": True,
        }

        self.minimum_weight = 0.0
        self.maximum_weight = 1.0

    def is_enabled(
        self,
        strategy_name: str,
    ) -> bool:
        return self.enabled_strategies.get(
            strategy_name,
            False,
        )

    def enable(
        self,
        strategy_name: str,
    ) -> None:
        self.enabled_strategies[strategy_name] = True

    def disable(
        self,
        strategy_name: str,
    ) -> None:
        self.enabled_strategies[strategy_name] = False

    def get_enabled_strategies(self) -> list[str]:
        return [
            strategy_name
            for strategy_name, enabled
            in self.enabled_strategies.items()
            if enabled
        ]

    def normalize_weight(
        self,
        weight: float,
    ) -> float:
        return max(
            self.minimum_weight,
            min(
                float(weight),
                self.maximum_weight,
            ),
        )