from strategy.gold_strategy import GoldStrategy
from strategy.trend_following_strategy import TrendFollowingStrategy
from strategy.breakout_strategy import BreakoutStrategy
from strategy.mean_reversion_strategy import MeanReversionStrategy
from strategy.scalping_strategy import ScalpingStrategy


class StrategyRegistry:

    def __init__(self):
        self._strategies = {
            "GoldStrategy": GoldStrategy(),
            "TrendFollowingStrategy": TrendFollowingStrategy(),
            "BreakoutStrategy": BreakoutStrategy(),
            "MeanReversionStrategy": MeanReversionStrategy(),
            "ScalpingStrategy": ScalpingStrategy(),
        }

    def get(self, strategy_name: str):
        if strategy_name not in self._strategies:
            raise ValueError(
                f"Strategia non registrata: {strategy_name}"
            )

        return self._strategies[strategy_name]

    def contains(self, strategy_name: str) -> bool:
        return strategy_name in self._strategies

    def get_names(self) -> list[str]:
        return list(self._strategies.keys())