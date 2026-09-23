from strategy.market_regime import (
    MarketRegimeDetector,
    MarketRegime,
)
from strategy.strategy_registry import StrategyRegistry
from strategy.strategy_portfolio_manager import (
    StrategyPortfolioManager,
)
from strategy.signal import Signal, SignalType
from strategy.breakout_strategy_selector import BreakoutStrategySelector


class StrategyEngine:

    def __init__(
        self,
        enabled_strategies: list[str] | None = None,
    ):
        self.registry = StrategyRegistry()

        self.market_regime_detector = MarketRegimeDetector()

        self.portfolio_manager = StrategyPortfolioManager(
            strategy_names=self.registry.get_names(),
            enabled_strategies=enabled_strategies,
        )
        self.breakout_strategy_selector = (
            BreakoutStrategySelector()
        )

        self.last_market_regime = MarketRegime.UNKNOWN
        self.last_strategy_name = "GoldStrategy"

    def _get_strategy_name(
        self,
        market_regime: MarketRegime,
        indicators,
    ) -> str:

        if market_regime == MarketRegime.TREND:
            trend_strategies = (
                "TrendFollowingStrategy",
                "TrendFollowingStrategyV2",
                "TrendFollowingStrategyV3",
            )

            enabled_trend_strategies = [
                strategy_name
                for strategy_name in trend_strategies
                if self.portfolio_manager.is_enabled(
                    strategy_name
                )
            ]

            if len(enabled_trend_strategies) == 1:
                return enabled_trend_strategies[0]

            return "TrendFollowingStrategy"

        if market_regime == MarketRegime.BREAKOUT:

            breakout_strategies = (
                "BreakoutStrategy",
                "BreakoutStrategyV2Base",
                "BreakoutStrategyV2",
            )

            enabled_breakout_strategies = [
                strategy_name
                for strategy_name in breakout_strategies
                if self.portfolio_manager.is_enabled(
                    strategy_name
                )
            ]

            # Benchmark isolato:
            # se una sola breakout è abilitata,
            # usa esattamente quella.
            if len(enabled_breakout_strategies) == 1:
                return enabled_breakout_strategies[0]

            # Funzionamento normale:
            # il selector sceglie in base al mercato.
            selected_strategy = (
                self.breakout_strategy_selector.select(
                    indicators
                )
            )
            if selected_strategy == "HOLD":
                return "HOLD"

            if (
                selected_strategy
                in enabled_breakout_strategies
            ):
                return selected_strategy

            if (
                "BreakoutStrategy"
                in enabled_breakout_strategies
            ):
                return "BreakoutStrategy"

            if enabled_breakout_strategies:
                return enabled_breakout_strategies[0]

            return "BreakoutStrategy"

        if market_regime == MarketRegime.RANGE:
            return "MeanReversionStrategy"

        if market_regime == MarketRegime.SCALPING:
            return "ScalpingStrategy"

        return "GoldStrategy"
        
    def generate_signal(
        self,
        indicators,
    ) -> Signal:

        market_regime = (
            self.market_regime_detector.detect(indicators)
        )

        self.last_market_regime = market_regime

        strategy_name = self._get_strategy_name(
            market_regime,
            indicators,
        )
        self.last_strategy_name = strategy_name

        print(
            "Market Regime:",
            market_regime.value,
        )
        if strategy_name == "HOLD":
            return Signal(
                signal=SignalType.HOLD,
                confidence=0.0,
            )

        if not self.portfolio_manager.is_enabled(
            strategy_name
        ):
            return Signal(
                signal=SignalType.HOLD,
                confidence=0.0,
            )

        strategy = self.registry.get(
            strategy_name
        )

        return strategy.generate(indicators)

    def get_last_market_regime(self):
        return self.last_market_regime

    def get_last_strategy_name(self):
        return self.last_strategy_name

    def get_available_strategies(self) -> list[str]:
        return (
            self.portfolio_manager
            .get_available_strategies()
        )

    def get_enabled_strategies(self) -> list[str]:
        return (
            self.portfolio_manager
            .get_enabled_strategies()
        )

    def enable_strategy(
        self,
        strategy_name: str,
    ) -> None:
        self.portfolio_manager.enable(
            strategy_name
        )

    def disable_strategy(
        self,
        strategy_name: str,
    ) -> None:
        self.portfolio_manager.disable(
            strategy_name
        )