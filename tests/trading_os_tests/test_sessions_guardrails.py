from datetime import date, datetime
from types import SimpleNamespace

from trading_os.services.guardrails import DayState, daily_state, daily_violations, trade_rule_violations
from trading_os.services.sessions import classify_session, in_trading_window

SESSIONS = [
    SimpleNamespace(name="Asia", start_utc="23:00", end_utc="08:00", active=False),
    SimpleNamespace(name="London", start_utc="07:00", end_utc="16:00", active=True),
    SimpleNamespace(name="New York", start_utc="12:00", end_utc="21:00", active=True),
]


def at(hour, minute=0, day=date(2026, 1, 14)):
    return datetime.combine(day, datetime.min.time()).replace(hour=hour, minute=minute)


def test_classify_session_with_overlap_and_midnight_wrap():
    assert classify_session(at(13), "UTC", SESSIONS) == "New York"  # London/NY overlap -> newest
    assert classify_session(at(7, 30), "UTC", SESSIONS) == "London"  # Asia/London overlap -> newest
    assert classify_session(at(2), "UTC", SESSIONS) == "Asia"
    assert classify_session(at(23, 30), "UTC", SESSIONS) == "Asia"
    assert classify_session(at(22), "UTC", SESSIONS) == ""


def test_classify_session_converts_account_time_zone_with_dst():
    # 14:00 in Rome is 13:00 UTC in winter and 12:00 UTC in summer: New York either way.
    assert classify_session(at(14), "Europe/Rome", SESSIONS) == "New York"
    assert classify_session(at(14, day=date(2026, 7, 14)), "Europe/Rome", SESSIONS) == "New York"
    # 07:30 in Rome is 06:30 UTC in winter (Asia) but 05:30 UTC in summer (still Asia);
    # 08:30 in Rome is London in winter (07:30 UTC) and Asia in summer (06:30 UTC).
    assert classify_session(at(8, 30), "Europe/Rome", SESSIONS) == "London"
    assert classify_session(at(8, 30, day=date(2026, 7, 14)), "Europe/Rome", SESSIONS) == "Asia"
    # 09:00 in Tokyo is 00:00 UTC.
    assert classify_session(at(9), "Asia/Tokyo", SESSIONS) == "Asia"
    # 08:30 in New York (winter, UTC-5) is 13:30 UTC.
    assert classify_session(at(8, 30), "America/New_York", SESSIONS) == "New York"


def test_trading_window_uses_active_sessions_only():
    assert in_trading_window(at(2), "UTC", SESSIONS) is False
    assert in_trading_window(at(10), "UTC", SESSIONS) is True
    assert (
        in_trading_window(at(10), "UTC", [SimpleNamespace(name="X", start_utc="01:00", end_utc="02:00", active=False)])
        is None
    )


def settings(**overrides):
    values = dict(
        currency="USD",
        min_rr=2.0,
        max_risk_pct=2.0,
        trade_rule_mode="block",
        max_daily_loss_pct=3.0,
        max_trades_per_day=0,
        daily_limit_mode="lock",
        require_checklist=True,
        warn_outside_sessions=True,
    )
    values.update(overrides)
    return SimpleNamespace(**values)


def codes(violations):
    return {v.code: v.severity for v in violations}


def test_trade_rules_follow_mode():
    common = dict(
        has_take_profit=True,
        risk_amount=100,
        equity=10_000,
        confluence_count=1,
        strategy_name="SMC",
        strategy_min_confluences=3,
        in_trading_window=False,
    )
    blocked = codes(trade_rule_violations(settings(), planned_rr=1.5, **common))
    assert blocked == {"RR_BELOW_MIN": "block", "CONFLUENCES_BELOW_MIN": "block", "OUTSIDE_SESSION": "warn"}
    warned = codes(trade_rule_violations(settings(trade_rule_mode="warn"), planned_rr=1.5, **common))
    assert warned["RR_BELOW_MIN"] == "warn"


def test_trade_rules_risk_cap_and_missing_target():
    result = codes(
        trade_rule_violations(
            settings(), planned_rr=None, has_take_profit=False, risk_amount=250, equity=10_000, confluence_count=0
        )
    )
    assert result == {"NO_TAKE_PROFIT": "warn", "RISK_ABOVE_MAX": "block"}
    assert (
        trade_rule_violations(
            settings(min_rr=0),
            planned_rr=None,
            has_take_profit=False,
            risk_amount=100,
            equity=10_000,
            confluence_count=0,
        )
        == []
    )


def day(**overrides):
    values = dict(
        today=date(2026, 1, 14),
        start_of_day_equity=10_000,
        current_equity=10_000,
        daily_loss_limit=300,
        realized_pnl_today=0,
        open_risk=0,
        open_trades=0,
        trades_today=0,
        checklist_complete=True,
    )
    values.update(overrides)
    return DayState(**values)


def test_daily_rules():
    assert daily_violations(settings(), day()) == []
    assert codes(daily_violations(settings(daily_limit_mode="warn"), day(checklist_complete=False))) == {
        "CHECKLIST_INCOMPLETE": "block"
    }
    assert codes(daily_violations(settings(), day(realized_pnl_today=-300))) == {"DAILY_LOSS_LIMIT": "block"}
    assert codes(daily_violations(settings(max_trades_per_day=3), day(trades_today=3))) == {
        "MAX_TRADES_REACHED": "block"
    }
    # 200 lost + 50 open risk leaves 50 of the 300 budget: a 100 risk could breach it.
    assert codes(
        daily_violations(settings(daily_limit_mode="warn"), day(realized_pnl_today=-200, open_risk=50), new_risk=100)
    ) == {"DAILY_RISK_BUDGET": "warn"}


def test_daily_state_levels():
    assert daily_state(settings(), day()) == "ok"
    assert daily_state(settings(), day(realized_pnl_today=-240)) == "warning"
    assert daily_state(settings(), day(realized_pnl_today=-310)) == "locked"
    assert daily_state(settings(daily_limit_mode="warn"), day(realized_pnl_today=-310)) == "breached"
    assert daily_state(settings(), day(realized_pnl_today=150)) == "ok"
