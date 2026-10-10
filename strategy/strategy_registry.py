from strategy.gold_strategy import GoldStrategy
from strategy.trend_following_strategy import TrendFollowingStrategy
from strategy.breakout_strategy import BreakoutStrategy
from strategy.mean_reversion_strategy import MeanReversionStrategy
from strategy.scalping_strategy import ScalpingStrategy
from strategy.breakout_strategy_v2 import BreakoutStrategyV2
from strategy.trend_following_strategy_v2 import (
    TrendFollowingStrategyV2,
)
from strategy.trend_following_strategy_v3 import (
    TrendFollowingStrategyV3,
)
from strategy.trend_following_strategy_v4 import (
    TrendFollowingStrategyV4,
)
from strategy.trend_following_strategy_v5 import (
    TrendFollowingStrategyV5,
)
from strategy.trend_following_strategy_v6 import (
    TrendFollowingStrategyV6,
)
from strategy.breakout_strategy_v3 import (
    BreakoutStrategyV3,
)
from strategy.mean_reversion_strategy_v2 import (
    MeanReversionStrategyV2,
)
from strategy.mean_reversion_strategy_v3 import (
    MeanReversionStrategyV3,
)
from strategy.scalping_strategy_v2 import (
    ScalpingStrategyV2,
)
from strategy.scalping_strategy_v3 import (
    ScalpingStrategyV3,
)

class StrategyRegistry:

    def __init__(self):
        self._strategies = {
            "GoldStrategy": GoldStrategy(),
            "TrendFollowingStrategy": TrendFollowingStrategy(),
            "TrendFollowingStrategyV2": TrendFollowingStrategyV2(),
            "TrendFollowingStrategyV3": TrendFollowingStrategyV3(),
            "TrendFollowingStrategyV4": TrendFollowingStrategyV4(),
            "TrendFollowingStrategyV5": TrendFollowingStrategyV5(),
            "TrendFollowingStrategyV6": TrendFollowingStrategyV6(),
            "BreakoutStrategy": BreakoutStrategy(),
            "BreakoutStrategyV2Base": BreakoutStrategyV2(
                use_candle_confirmation=False
            ),
            "BreakoutStrategyV2": BreakoutStrategyV2(
                use_candle_confirmation=True
            ),
            "BreakoutStrategyV3": BreakoutStrategyV3(),
            "MeanReversionStrategy": MeanReversionStrategy(),
            "MeanReversionStrategyV2": MeanReversionStrategyV2(),
            "MeanReversionStrategyV3": MeanReversionStrategyV3(),
            "ScalpingStrategy": ScalpingStrategy(),
            "ScalpingStrategyV2": ScalpingStrategyV2(),
            "ScalpingStrategyV3": ScalpingStrategyV3(),
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