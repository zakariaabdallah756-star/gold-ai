from collections import defaultdict

from backtest.strategy_performance import (
    StrategyPerformance,
)


class StrategyPerformanceCalculator:

    def calculate(
        self,
        closed_positions,
    ) -> list[StrategyPerformance]:
        grouped_positions = defaultdict(list)

        for position in closed_positions:
            key = (
                position.strategy_name,
                position.market_regime,
            )

            grouped_positions[key].append(position)

        results = []

        for (
            strategy_name,
            market_regime,
        ), positions in grouped_positions.items():

            profits = [
                float(position.profit)
                for position in positions
            ]

            total_trades = len(profits)

            winning_trades = sum(
                1 for profit in profits
                if profit > 0
            )

            losing_trades = sum(
                1 for profit in profits
                if profit < 0
            )

            net_profit = sum(profits)

            if total_trades > 0:
                win_rate = (
                    winning_trades / total_trades
                ) * 100
            else:
                win_rate = 0.0

            winning_profit = sum(
                profit for profit in profits
                if profit > 0
            )

            losing_profit = abs(
                sum(
                    profit for profit in profits
                    if profit < 0
                )
            )

            if losing_profit > 0:
                profit_factor = (
                    winning_profit / losing_profit
                )
            elif winning_profit > 0:
                profit_factor = float("inf")
            else:
                profit_factor = 0.0

            results.append(
                StrategyPerformance(
                    strategy_name=strategy_name,
                    market_regime=market_regime,
                    total_trades=total_trades,
                    winning_trades=winning_trades,
                    losing_trades=losing_trades,
                    net_profit=net_profit,
                    win_rate=win_rate,
                    profit_factor=profit_factor,
                )
            )

        return sorted(
            results,
            key=lambda result: result.net_profit,
            reverse=True,
        )