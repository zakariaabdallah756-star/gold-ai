class StrategyPortfolioManager:

    def __init__(
        self,
        strategy_names: list[str] | None = None,
        enabled_strategies: list[str] | set[str] | tuple[str, ...] | None = None,
    ):
        if strategy_names is None:
            strategy_names = [
                "GoldStrategy",
                "TrendFollowingStrategy",
                "BreakoutStrategy",
                "MeanReversionStrategy",
                "ScalpingStrategy",
            ]

        self.available_strategies = list(strategy_names)

        if enabled_strategies is None:
            enabled_set = set(self.available_strategies)
        else:
            enabled_set = set(enabled_strategies)

            unknown_strategies = (
                enabled_set - set(self.available_strategies)
            )

            if unknown_strategies:
                unknown_names = ", ".join(
                    sorted(unknown_strategies)
                )

                raise ValueError(
                    "Strategie non disponibili: "
                    f"{unknown_names}"
                )

        self.enabled_strategies = {
            strategy_name: strategy_name in enabled_set
            for strategy_name in self.available_strategies
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
        self._validate_strategy(strategy_name)
        self.enabled_strategies[strategy_name] = True

    def disable(
        self,
        strategy_name: str,
    ) -> None:
        self._validate_strategy(strategy_name)
        self.enabled_strategies[strategy_name] = False

    def enable_only(
        self,
        strategy_name: str,
    ) -> None:
        self._validate_strategy(strategy_name)

        for available_strategy in self.available_strategies:
            self.enabled_strategies[available_strategy] = (
                available_strategy == strategy_name
            )

    def enable_all(self) -> None:
        for strategy_name in self.available_strategies:
            self.enabled_strategies[strategy_name] = True

    def disable_all(self) -> None:
        for strategy_name in self.available_strategies:
            self.enabled_strategies[strategy_name] = False

    def get_available_strategies(self) -> list[str]:
        return list(self.available_strategies)

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

    def _validate_strategy(
        self,
        strategy_name: str,
    ) -> None:
        if strategy_name not in self.available_strategies:
            raise ValueError(
                f"Strategia non disponibile: {strategy_name}"
            )