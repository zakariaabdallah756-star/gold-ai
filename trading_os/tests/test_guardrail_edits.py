"""Guardrails on edits and after-the-fact logs: reopening, raised risk, time zones, sessions, recorded flags."""

from datetime import datetime, timedelta

from .conftest import complete_checklist, now_utc, trade_payload, update_settings, yesterday_iso


def codes(violations) -> set[str]:
    return {v["code"] for v in violations}


def at_today(hour: int, minute: int = 0) -> str:
    return datetime.combine(now_utc().date(), datetime.min.time()).replace(hour=hour, minute=minute).isoformat()


def strategy_id(client, name: str) -> int:
    return next(s["id"] for s in client.get("/api/strategies").json() if s["name"] == name)


def test_reopening_a_logged_trade_is_judged_as_a_live_entry(client):
    complete_checklist(client)
    update_settings(client, warn_outside_sessions=False)
    client.post("/api/killswitch", json={"hours": 12, "reason": "Tilt"})
    closed = trade_payload(
        status="closed", exit_price=2650.0, pnl=0.0, entry_time=(now_utc() + timedelta(seconds=1)).isoformat()
    )
    logged = client.post("/api/trades", json=closed)
    assert logged.status_code == 201
    trade = logged.json()["trade"]

    reopened = client.put(
        f"/api/trades/{trade['id']}", json={**closed, "status": "open", "exit_price": None, "pnl": None}
    )
    assert reopened.status_code == 423 and "KILL_SWITCH" in codes(reopened.json()["violations"])
    assert client.get(f"/api/trades/{trade['id']}").json()["status"] == "closed"


def test_raising_the_risk_of_an_open_trade_is_checked_but_managing_it_is_not(client):
    complete_checklist(client)
    update_settings(client, warn_outside_sessions=False)
    trade = client.post("/api/trades", json=trade_payload()).json()["trade"]  # 0.2 lots = 1% risk

    oversized = client.put(
        f"/api/trades/{trade['id']}", json=trade_payload(entry_time=trade["entry_time"], position_size=1.0)
    )
    assert oversized.status_code == 423 and "RISK_ABOVE_MAX" in codes(oversized.json()["violations"])

    client.post("/api/killswitch", json={"hours": 4})
    # Moving the target (same risk) of a trade opened before the cool-off stays possible.
    moved = client.put(
        f"/api/trades/{trade['id']}", json=trade_payload(entry_time=trade["entry_time"], take_profit=2670.0)
    )
    assert moved.status_code == 200, moved.text
    assert "KILL_SWITCH" not in codes(moved.json()["trade"]["violations"])


def test_reopen_after_daily_loss_counts_the_trade_once(client):
    complete_checklist(client)
    update_settings(client, warn_outside_sessions=False, max_trades_per_day=1)
    closed = trade_payload(status="closed", exit_price=2655.0, pnl=100.0)
    trade = client.post("/api/trades", json=closed).json()["trade"]
    # The trade is today's only trade: reopening it is not "a second trade".
    reopened = client.put(
        f"/api/trades/{trade['id']}",
        json={**closed, "entry_time": trade["entry_time"], "status": "open", "exit_price": None, "pnl": None},
    )
    assert reopened.status_code == 200, reopened.text


def test_time_zone_change_keeps_the_cool_off_and_news_times_in_place(client):
    status = client.post("/api/killswitch", json={"hours": 12}).json()
    until_utc = datetime.fromisoformat(status["cooloff_until"])
    client.post("/api/news", json={"time": at_today(23, 30), "title": "CPI", "currency": "USD", "impact": "high"})

    update_settings(client, timezone="Pacific/Kiritimati")  # UTC+14
    status = client.get("/api/risk/status").json()
    assert status["can_trade"] is False and "KILL_SWITCH" in codes(status["reasons"])
    assert datetime.fromisoformat(status["cooloff_until"]) == until_utc + timedelta(hours=14)
    event = client.get(
        "/api/news",
        params={
            "date_from": now_utc().date().isoformat(),
            "date_to": (now_utc().date() + timedelta(days=2)).isoformat(),
        },
    ).json()[0]
    assert datetime.fromisoformat(event["time"]) == datetime.fromisoformat(at_today(23, 30)) + timedelta(hours=14)


def test_strategy_session_accepts_an_allowed_session_that_overlaps_a_later_one(client):
    complete_checklist(client)
    sid = strategy_id(client, "Price Action")
    strategy = client.get("/api/strategies").json()
    body = next(s for s in strategy if s["id"] == sid)
    assert client.put(f"/api/strategies/{sid}", json={**body, "allowed_sessions": ["London"]}).status_code == 200
    payload = trade_payload(
        entry_time=at_today(13, 30), strategy="Price Action", confluences=["Key level (S/R)", "Break of structure"]
    )
    check = client.post("/api/trades/check", json=payload).json()
    assert check["session"] == "New York"  # filed under the newest session...
    assert "STRATEGY_SESSION" not in codes(check["violations"])  # ...but London is open too
    late = client.post("/api/trades/check", json={**payload, "entry_time": at_today(18, 0)}).json()
    assert "STRATEGY_SESSION" in codes(late["violations"])


def test_after_the_fact_logs_are_judged_against_the_day_at_their_entry_time(client):
    complete_checklist(client)
    update_settings(client, warn_outside_sessions=False, max_trades_per_day=2)
    times = [at_today(11, 38), at_today(11, 8), at_today(10, 38)]  # logged newest first, at day end
    results = {}
    for when in times:
        response = client.post(
            "/api/trades", json=trade_payload(status="closed", entry_time=when, exit_time=when, exit_price=2655.0)
        )
        assert response.status_code == 201, response.text
        results[when] = codes(response.json()["warnings"])
    # Each was judged with the trades entered before it at the time of logging.
    assert "MAX_TRADES_REACHED" not in results[at_today(10, 38)]


def test_news_window_follows_a_corrected_entry_time(client):
    update_settings(client, news_mode="block", warn_outside_sessions=False)
    client.post("/api/news", json={"time": yesterday_iso(), "title": "NFP", "currency": "USD", "impact": "high"})
    payload = trade_payload(status="closed", entry_time=yesterday_iso(), exit_time=yesterday_iso(), exit_price=2655.0)
    trade = client.post("/api/trades", json=payload).json()["trade"]
    assert "NEWS_WINDOW" in codes(trade["violations"]) and trade["rule_compliant"] is False

    moved = (datetime.fromisoformat(yesterday_iso()) - timedelta(hours=4)).isoformat()
    edited = client.put(f"/api/trades/{trade['id']}", json={**payload, "entry_time": moved, "exit_time": moved}).json()[
        "trade"
    ]
    assert "NEWS_WINDOW" not in codes(edited["violations"]) and edited["rule_compliant"] is True


def test_editing_only_notes_keeps_the_recorded_verdict(client):
    update_settings(client, min_rr=1.5, warn_outside_sessions=False)
    payload = trade_payload(
        status="closed", entry_time=yesterday_iso(), exit_time=yesterday_iso(), take_profit=2658.0, exit_price=2655.0
    )
    trade = client.post("/api/trades", json=payload).json()["trade"]
    assert "RR_BELOW_MIN" not in codes(trade["violations"])
    update_settings(client, min_rr=2.0)
    edited = client.put(f"/api/trades/{trade['id']}", json={**payload, "notes": "Reviewed"}).json()["trade"]
    assert "RR_BELOW_MIN" not in codes(edited["violations"])
    # Changing a trade-defining field re-judges it against the current plan.
    resized = client.put(f"/api/trades/{trade['id']}", json={**payload, "position_size": 0.1}).json()["trade"]
    assert "RR_BELOW_MIN" in codes(resized["violations"])


def test_corrected_entry_time_is_filed_under_the_right_session(client):
    payload = trade_payload(
        status="closed",
        entry_time=(datetime.fromisoformat(yesterday_iso()).replace(hour=3)).isoformat(),
        exit_price=2655.0,
    )
    trade = client.post("/api/trades", json=payload).json()["trade"]
    assert trade["session"] == "Asia"
    corrected = (datetime.fromisoformat(yesterday_iso()).replace(hour=19)).isoformat()
    edited = client.put(
        f"/api/trades/{trade['id']}", json={**payload, "entry_time": corrected, "session": "Asia"}
    ).json()["trade"]
    assert edited["session"] == "New York"


def test_informational_notices_do_not_mark_trades_as_rule_breaking(client):
    created = client.post("/api/backtest/strategies", json={"name": "Asia range"})
    assert created.status_code == 201, created.text
    payload = trade_payload(
        status="closed", entry_time=yesterday_iso(), exit_time=yesterday_iso(), exit_price=2655.0, strategy="Asia range"
    )
    update_settings(client, warn_outside_sessions=False)
    trade = client.post("/api/trades", json=payload).json()["trade"]
    assert "STRATEGY_INCUBATOR" in codes(trade["violations"]) and trade["rule_compliant"] is True
