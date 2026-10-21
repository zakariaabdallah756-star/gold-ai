from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

from trading_os.services import analytics


def make_trades(pnls, risk=50.0):
    start = datetime(2026, 1, 5, 9, 0)  # a Monday
    trades = []
    for index, pnl in enumerate(pnls):
        entry = start + timedelta(days=index)
        outcome = "win" if pnl > 0 else "loss" if pnl < 0 else "breakeven"
        trades.append(
            SimpleNamespace(
                id=index + 1,
                symbol="XAUUSD" if index % 2 == 0 else "EURUSD",
                direction="long",
                strategy="SMC",
                session="London",
                emotion="Calm",
                timeframe="M15",
                confluences=["Liquidity sweep", "Key level"] if index % 2 == 0 else ["Key level"],
                pnl=pnl,
                fees=1.0,
                r_multiple=pnl / risk,
                planned_rr=2.0,
                executed_rr=pnl / risk,
                outcome=outcome,
                rule_compliant=index % 2 == 0,
                entry_time=entry,
                exit_time=entry + timedelta(hours=2),
                close_time=entry + timedelta(hours=2),
            )
        )
    return trades


def test_core_stats():
    stats = analytics.core_stats(make_trades([100, -50, 200, -50, 0]))
    assert stats["trades"] == 5
    assert (stats["wins"], stats["losses"], stats["breakeven"]) == (2, 2, 1)
    assert stats["win_rate"] == 40.0
    assert stats["net_pnl"] == 200.0
    assert stats["profit_factor"] == 3.0
    assert stats["expectancy"] == 40.0
    assert stats["avg_r"] == pytest.approx(0.8)


def test_profit_factor_without_losses_is_infinite():
    stats = analytics.core_stats(make_trades([100, 50]))
    assert stats["profit_factor"] is None
    assert stats["profit_factor_infinite"] is True
    assert analytics.core_stats([])["win_rate"] is None


def test_summary_drawdown_streaks_and_compliance():
    trades = make_trades([100, -50, -100, 200, -10, -10, -10])
    summary = analytics.summarize(trades, 1000)
    # Equity: 1100, 1050, 950, 1150, 1140, 1130, 1120 -> deepest fall 1100 -> 950.
    assert summary["max_drawdown"] == 150.0
    assert summary["max_drawdown_pct"] == pytest.approx(13.64, abs=0.01)
    assert summary["max_consecutive_losses"] == 3
    assert summary["max_consecutive_wins"] == 1
    assert summary["current_streak"] == -3
    assert summary["end_equity"] == 1120.0
    assert summary["avg_hold_minutes"] == 120.0
    assert summary["compliance_rate"] == pytest.approx(57.14, abs=0.01)
    assert summary["followed_plan"]["trades"] == 4
    assert summary["payoff_ratio"] == 4.17  # avg win 150 / |avg loss 36|


def test_equity_curve_and_daily_pnl():
    trades = make_trades([100, -50])
    curve = analytics.equity_curve(trades, 1000)
    assert [p["equity"] for p in curve] == [1000, 1100, 1050]
    daily = analytics.daily_pnl(trades)
    assert [d["cumulative"] for d in daily] == [100, 50]


def test_r_distribution_bins_and_clamping():
    bins = analytics.r_distribution(make_trades([-50, -400, 350, 125, 0], risk=50))
    by_label = {b["label"]: b["count"] for b in bins}
    assert by_label["-1R"] == 1
    assert by_label["≤-3R"] == 1  # -8R clamps into the first bin
    assert by_label["≥+5R"] == 1  # +7R clamps into the last bin
    assert by_label["+2.5R"] == 1
    assert by_label["0R"] == 1
    assert sum(b["count"] for b in bins) == 5


def test_breakdowns_including_overlapping_confluences():
    result = analytics.breakdowns(make_trades([100, -50, 200]))
    assert {row["key"]: row["trades"] for row in result["symbol"]} == {"XAUUSD": 2, "EURUSD": 1}
    assert {row["key"]: row["trades"] for row in result["confluence"]} == {"Key level": 3, "Liquidity sweep": 2}
    assert [row["key"] for row in result["weekday"]] == ["Monday", "Tuesday", "Wednesday"]
    assert {row["key"] for row in result["compliance"]} == {"Followed plan", "Broke rules"}


def test_r_distribution_puts_half_way_values_in_the_upper_bin_consistently():
    bins = analytics.r_distribution(make_trades([12.5, 37.5, -12.5, -37.5], risk=50))  # +0.25R +0.75R -0.25R -0.75R
    by_label = {b["label"]: b["count"] for b in bins}
    assert (by_label["+0.5R"], by_label["+1R"], by_label["0R"], by_label["-0.5R"]) == (1, 1, 1, 1)
