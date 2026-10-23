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


# ------------------------------------------------------------------ bookings (partial fills)


def fill(when, kind, lots, pnl, event_id, target_level=None):
    return SimpleNamespace(
        id=event_id, time=when, kind=kind, lots=lots, pnl=pnl, price=2650.0, target_level=target_level
    )


def managed_trade(events, *, pnl=None, fees=0.0, status="closed", trade_id=1):
    fills = [e for e in events if e.lots]
    exit_time = max(e.time for e in fills) if status == "closed" else None
    entry = min(e.time for e in events) - timedelta(hours=1)
    trade = make_trades([pnl or 0.0])[0]
    trade.__dict__.update(
        id=trade_id,
        managed=True,
        status=status,
        events=events,
        pnl=pnl,
        fees=fees,
        outcome=None if pnl is None else "win" if pnl > 0 else "loss" if pnl < 0 else "breakeven",
        entry_time=entry,
        exit_time=exit_time,
        close_time=exit_time or entry,
    )
    return trade


DAY1 = datetime(2026, 3, 2, 10, 0)
DAY2 = datetime(2026, 3, 3, 15, 0)


def scale_out_over_two_days():
    """+100 partial on day 1, a stop move, then the rest closed for -200 on day 2 with 4 of fees."""
    return managed_trade(
        [
            fill(DAY2, "close", 0.2, -200.0, 3),  # listed out of order on purpose
            fill(DAY1, "partial", 0.1, 100.0, 1),
            SimpleNamespace(id=2, time=DAY1 + timedelta(hours=1), kind="stop_move", lots=None, pnl=None),
        ],
        pnl=-104.0,
        fees=4.0,
    )


def test_managed_trade_books_each_fill_on_its_fill_time_and_fees_at_the_close():
    trade = scale_out_over_two_days()
    booked = analytics.trade_bookings(trade)
    assert [(b.time, b.kind, b.amount, b.closes) for b in booked] == [
        (DAY1, "partial", 100.0, False),
        (DAY2, "close", -204.0, True),
    ]
    assert sum(b.amount for b in booked) == trade.pnl
    assert (booked[0].lots, booked[0].event_id, booked[1].event_id) == (0.1, 1, 3)


def test_closing_booking_takes_the_residual_so_bookings_add_up_to_the_trade_pnl():
    fills = [fill(DAY1 + timedelta(minutes=i), "tp", 0.1, 33.33 + (i == 2) * 0.01, i + 1, i + 1) for i in range(3)]
    trade = managed_trade(fills, pnl=round(100.0 - 0.01, 2), fees=0.01)
    booked = analytics.trade_bookings(trade)
    assert [b.amount for b in booked] == [33.33, 33.33, 33.33]
    assert round(sum(b.amount for b in booked), 2) == trade.pnl == 99.99
    assert [b.target_level for b in booked] == [1, 2, 3]


def test_open_and_unmanaged_trades():
    open_trade = managed_trade([fill(DAY1, "partial", 0.1, 50.0, 1)], status="open", fees=3.0)
    booked = analytics.trade_bookings(open_trade)
    assert [(b.amount, b.closes) for b in booked] == [(50.0, False)]  # gross: the fees wait for the close

    legacy = make_trades([75.0])[0]  # no managed/status/events attributes, like a backtest row
    assert [(b.time, b.amount, b.kind, b.closes) for b in analytics.trade_bookings(legacy)] == [
        (legacy.close_time, 75.0, "close", True)
    ]
    legacy_open = make_trades([0.0])[0]
    legacy_open.__dict__.update(status="open", pnl=None, managed=False, events=[])
    assert analytics.trade_bookings(legacy_open) == []


def test_bookings_are_windowed_and_in_time_order():
    early, late = make_trades([10.0, 20.0])
    trade = scale_out_over_two_days()
    trade.id = 9
    booked = analytics.bookings([trade, late, early])
    assert [(b.trade.id, b.amount) for b in booked] == [(1, 10.0), (2, 20.0), (9, 100.0), (9, -204.0)]
    window = analytics.bookings([trade], since=datetime(2026, 3, 3), before=datetime(2026, 3, 4))
    assert [b.amount for b in window] == [-204.0]


def test_daily_pnl_and_equity_follow_the_fill_days_but_trade_stats_count_the_trade_once():
    trade = scale_out_over_two_days()
    daily = analytics.daily_pnl([trade])
    assert daily == [
        {"date": "2026-03-02", "pnl": 100.0, "trades": 0, "wins": 0, "losses": 0, "partials": 1, "cumulative": 100.0},
        {"date": "2026-03-03", "pnl": -204.0, "trades": 1, "wins": 0, "losses": 1, "partials": 0, "cumulative": -104.0},
    ]

    report = analytics.full_report([trade], 1000)
    assert [p["equity"] for p in report["equity_curve"]] == [1000, 1100, 896]
    assert [(p["kind"], p["closes"], p["trade_id"]) for p in report["equity_curve"][1:]] == [
        ("partial", False, 1),
        ("close", True, 1),
    ]
    summary = report["summary"]
    assert (summary["trades"], summary["losses"], summary["net_pnl"]) == (1, 1, -104.0)
    assert summary["realized_pnl"] == -104.0 and summary["end_equity"] == 896.0
    # The drawdown runs from the peak reached after the partial, not from the starting balance.
    assert summary["max_drawdown"] == 204.0 and summary["max_drawdown_pct"] == 18.55
    assert report["daily_pnl"] == daily


def test_explicit_bookings_split_period_pnl_from_trade_statistics():
    """A date-filtered report: the trade closed in range, its partial was booked before the range."""
    trade = scale_out_over_two_days()
    in_range = analytics.bookings([trade], since=datetime(2026, 3, 3))
    report = analytics.full_report([trade], 1100, in_range)
    assert report["summary"]["net_pnl"] == -104.0 and report["summary"]["trades"] == 1
    assert report["summary"]["realized_pnl"] == -204.0 and report["summary"]["end_equity"] == 896.0
    assert [d["date"] for d in report["daily_pnl"]] == ["2026-03-03"]

    open_trade = managed_trade([fill(DAY1, "partial", 0.1, 50.0, 1)], status="open")
    only_partials = analytics.full_report([], 1000, analytics.bookings([open_trade]))
    assert only_partials["summary"]["trades"] == 0 and only_partials["summary"]["realized_pnl"] == 50.0
    assert [(d["pnl"], d["trades"], d["partials"]) for d in only_partials["daily_pnl"]] == [(50.0, 0, 1)]


def test_booking_stats():
    trade = scale_out_over_two_days()
    stats = analytics.booking_stats(analytics.bookings([trade, *make_trades([30.0])]))
    assert stats == {"pnl": -74.0, "trades": 2, "wins": 1, "losses": 1, "win_rate": 50.0, "partials": 1}
    assert analytics.booking_stats([]) == {
        "pnl": 0.0,
        "trades": 0,
        "wins": 0,
        "losses": 0,
        "win_rate": None,
        "partials": 0,
    }


def test_summary_without_bookings_keeps_the_per_trade_behaviour():
    trades = make_trades([100, -50])
    summary = analytics.summarize(trades, 1000)
    assert summary["realized_pnl"] == summary["net_pnl"] == 50.0 and summary["end_equity"] == 1050.0
    assert analytics.summarize(trades, 1000, analytics.bookings(trades)) == summary
