from collections import defaultdict, deque


class StrategyPerformanceTracker:

    def __init__(
        self,
        window_size: int = 20,
    ):
        if window_size <= 0:
            raise ValueError(
                "window_size deve essere maggiore di zero."
            )

        self.window_size = window_size

        self.strategy_profits = defaultdict(
            lambda: deque(
                maxlen=self.window_size
            )
        )

    def record_trade(
        self,
        strategy_name: str,
        profit: float,
    ) -> None:
        self.strategy_profits[strategy_name].append(
            float(profit)
        )

    def get_trade_count(
        self,
        strategy_name: str,
    ) -> int:
        return len(
            self.strategy_profits[strategy_name]
        )

    def get_net_profit(
        self,
        strategy_name: str,
    ) -> float:
        return sum(
            self.strategy_profits[strategy_name]
        )

    def get_win_rate(
        self,
        strategy_name: str,
    ) -> float:
        profits = self.strategy_profits[
            strategy_name
        ]

        if not profits:
            return 0.0

        winning_trades = sum(
            1
            for profit in profits
            if profit > 0
        )

        return (
            winning_trades / len(profits)
        ) * 100

    def get_profit_factor(
        self,
        strategy_name: str,
    ) -> float:
        profits = self.strategy_profits[
            strategy_name
        ]

        gross_profit = sum(
            profit
            for profit in profits
            if profit > 0
        )

        gross_loss = abs(
            sum(
                profit
                for profit in profits
                if profit < 0
            )
        )

        if gross_loss > 0:
            return gross_profit / gross_loss

        if gross_profit > 0:
            return float("inf")

        return 0.0

    def get_score(
        self,
        strategy_name: str,
    ) -> float:
        trade_count = self.get_trade_count(
            strategy_name
        )

        if trade_count < 3:
            return 1.0

        profit_factor = self.get_profit_factor(
            strategy_name
        )

        win_rate = self.get_win_rate(
            strategy_name
        )

        net_profit = self.get_net_profit(
            strategy_name
        )

        if profit_factor == float("inf"):
            profit_factor_score = 2.0
        else:
            profit_factor_score = min(
                profit_factor,
                2.0,
            )

        win_rate_score = min(
            win_rate / 50.0,
            2.0,
        )

        profit_score = (
            1.0
            if net_profit >= 0
            else 0.5
        )

        score = (
            profit_factor_score
            + win_rate_score
            + profit_score
        ) / 3

        return round(score, 4)

    def get_summary(self) -> dict:
        summary = {}

        for strategy_name in self.strategy_profits:
            summary[strategy_name] = {
                "trades": self.get_trade_count(
                    strategy_name
                ),
                "net_profit": self.get_net_profit(
                    strategy_name
                ),
                "win_rate": self.get_win_rate(
                    strategy_name
                ),
                "profit_factor": (
                    self.get_profit_factor(
                        strategy_name
                    )
                ),
                "score": self.get_score(
                    strategy_name
                ),
            }

        return summary

    def reset(self) -> None:
        self.strategy_profits.clear()