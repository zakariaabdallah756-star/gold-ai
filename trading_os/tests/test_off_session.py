"""Off-session guardrail: warn or block entries outside active sessions; quiet hours for notifications."""

from datetime import datetime, timedelta

import pytest
from sqlalchemy import select

from trading_os.models import NotificationLog, StrategyAlert
from trading_os.services import account, alerts, notify, notify_channels
from trading_os.services.account import get_settings

from .conftest import complete_checklist, now_utc, trade_payload, update_settings
from .test_alerts_engine import feed, make_rule, make_strategy, series
from .test_notifications import CONFIGS, alert_note


def only_window(client, start_hours: float, end_hours: float) -> None:
    """Make one session, from now + start_hours to now + end_hours (UTC), the only active one."""
    mine = None
    for session in client.get("/api/sessions").json():
        if session["name"] == "My window":
            mine = session
        else:
            client.put(f"/api/sessions/{session['id']}", json={**session, "active": False})
    now = now_utc()
    body = {
        "name": "My window",
        "start_utc": (now + timedelta(hours=start_hours)).strftime("%H:%M"),
        "end_utc": (now + timedelta(hours=end_hours)).strftime("%H:%M"),
        "active": True,
    }
    if mine:
        assert client.put(f"/api/sessions/{mine['id']}", json={**mine, **body}).status_code == 200
    else:
        assert client.post("/api/sessions", json=body).status_code == 201


def outside_now(client):
    only_window(client, 3, 4)


def inside_now(client):
    only_window(client, -1, 1)


@pytest.fixture
def pushes(monkeypatch):
    calls = []

    def fake_post(url, body, headers):
        import httpx

        calls.append(url)
        return httpx.Response(200, json={"ok": True})

    monkeypatch.setattr(notify_channels, "_http_post", fake_post)
    return calls


def quiet(client, on=True):
    body = {"enabled": True, "events": ["strategy_alert", "risk", "news"], "quiet_outside_sessions": on}
    response = client.put("/api/notifications/settings", json=body)
    assert response.status_code == 200, response.text
    return response.json()


def send(client, note):
    with client.app.state.session_factory() as db:
        row = notify.send(db, account.get_settings(db), note)
        return None if row is None else row.status


def test_off_session_entries_warn_by_default_and_can_block_live_entries(client):
    complete_checklist(client)
    outside_now(client)
    check = client.post("/api/trades/check", json=trade_payload()).json()
    assert {v["code"]: v["severity"] for v in check["violations"]}["OUTSIDE_SESSION"] == "warn"
    assert check["blocked"] is False

    update_settings(client, session_rule_mode="block")
    check = client.post("/api/trades/check", json=trade_payload()).json()
    assert {v["code"]: v["severity"] for v in check["violations"]}["OUTSIDE_SESSION"] == "block"
    assert check["blocked"] is True
    assert client.post("/api/trades", json=trade_payload()).status_code == 423

    # A trade logged after the fact is still saved, with the rule recorded as a warning.
    earlier = (now_utc() - timedelta(days=1)).isoformat()
    logged = client.post("/api/trades", json=trade_payload(status="closed", entry_time=earlier, exit_price=2655.0))
    assert logged.status_code == 201
    assert {v["code"]: v["severity"] for v in logged.json()["warnings"]}["OUTSIDE_SESSION"] == "warn"

    inside_now(client)
    assert client.post("/api/trades/check", json=trade_payload()).json()["blocked"] is False
    assert client.put("/api/settings", json={"session_rule_mode": "sometimes"}).status_code == 422


def test_quiet_hours_hold_alerts_and_news_but_never_risk_events(client, pushes):
    client.post("/api/notifications/channels", json={"kind": "ntfy", "config": CONFIGS["ntfy"]})
    outside_now(client)
    settings = quiet(client)
    assert settings["quiet_outside_sessions"] is True and settings["in_trading_window_now"] is False
    assert settings["active_sessions"] == ["My window"]

    assert send(client, alert_note()) == "held" and pushes == []
    assert send(client, alert_note(event="news", title="US CPI in 10 min")) == "held" and pushes == []
    assert send(client, alert_note(event="risk", title="Daily loss limit reached")) == "sent" and len(pushes) == 1
    assert send(client, notify.test_note()) == "sent" and len(pushes) == 2

    log = client.get("/api/notifications/log").json()
    held = [row for row in log if row["status"] == "held"]
    assert len(held) == 2 and all(row["results"] == [] for row in held)

    inside_now(client)
    assert send(client, alert_note()) == "sent"
    outside_now(client)
    quiet(client, on=False)
    assert send(client, alert_note()) == "sent"


def test_quiet_hours_do_nothing_without_active_sessions(client, pushes):
    client.post("/api/notifications/channels", json={"kind": "ntfy", "config": CONFIGS["ntfy"]})
    for session in client.get("/api/sessions").json():
        client.put(f"/api/sessions/{session['id']}", json={**session, "active": False})
    assert quiet(client)["in_trading_window_now"] is None
    assert send(client, alert_note()) == "sent"


def test_a_held_news_reminder_is_not_repeated(client, pushes):
    client.post("/api/notifications/channels", json={"kind": "ntfy", "config": CONFIGS["ntfy"]})
    outside_now(client)
    quiet(client)
    client.put("/api/notifications/settings", json={"news_reminder_min": 15})
    when = (now_utc() + timedelta(minutes=10)).isoformat()
    client.post("/api/news", json={"time": when, "title": "US CPI", "currency": "USD", "impact": "high"})
    scheduler = client.app.state.scheduler
    scheduler.run_now("news-reminders")
    scheduler.run_now("news-reminders")
    news = [row for row in client.get("/api/notifications/log").json() if row["event"] == "news"]
    assert [row["status"] for row in news] == ["held"] and pushes == []


def test_held_alerts_stay_on_the_alerts_page(client, monkeypatch, pushes):
    client.post("/api/notifications/channels", json={"kind": "ntfy", "config": CONFIGS["ntfy"]})
    outside_now(client)
    quiet(client)
    feed(monkeypatch)
    with client.app.state.session_factory() as db:
        make_rule(db)
        (row,) = alerts.scan(db, get_settings(db), now_utc())
        assert row["status"] == "alert"
        alert = db.get(StrategyAlert, row["alert_id"])
        assert db.get(NotificationLog, alert.notification_id).status == "held"
    assert pushes == []
    listed = client.get("/api/alerts").json()["alerts"][0]
    assert listed["notification_status"] == "held"
    assert client.get(f"/api/alerts/{listed['id']}").json()["notification_status"] == "held"


def test_alerts_respect_session_overlaps_like_trades(client):
    with client.app.state.session_factory() as db:
        london = make_strategy(db, name="London only", allowed_sessions=["London"])
        rule = make_rule(db, strategy_id=london.id)
        overlap = series(last_open=datetime(2026, 10, 20, 13, 45))  # closes 14:00 UTC: London and New York
        ev = alerts.evaluate(db, get_settings(db), rule, "XAUUSD", overlap, datetime(2026, 10, 20, 14, 1))
        assert ev.session == "New York"
        assert "STRATEGY_SESSION" not in {v.code for v in ev.copilot} and ev.found
        assert db.scalar(select(StrategyAlert)) is None  # evaluating never records anything
