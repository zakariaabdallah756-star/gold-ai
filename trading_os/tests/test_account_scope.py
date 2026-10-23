"""Every page follows the trading account shown: analytics, journal, calendar, reviews, positions, reports, prop
challenges, alert sizing, risk notifications (v1.5)."""

from datetime import timedelta

from sqlalchemy import select

from trading_os.models import Trade
from trading_os.services import alerts
from trading_os.services.account import get_settings

from .conftest import complete_checklist, now_utc, trade_payload, update_settings
from .test_accounts import new_account
from .test_alerts_engine import FIXED_LAST_OPEN, PIN, SWEEP, make_rule, series
from .test_notifications import add_channel, enable
from .test_notifications import http as http  # noqa: F401 - pytest fixture used below
from .test_notifications import risk_log
from .test_prop import challenge, closed_trade


def setup_two_accounts(client):
    complete_checklist(client)
    update_settings(client, warn_outside_sessions=False)
    main_id = client.get("/api/settings").json()["default_account_id"]
    prop = new_account(client, starting_balance=100_000.0)
    yesterday = (now_utc() - timedelta(days=1)).replace(hour=10).isoformat()
    closed = trade_payload(status="closed", entry_time=yesterday, exit_price=2660.0)
    client.post("/api/trades", json={**closed, "pnl": 300.0, "account_id": main_id})
    client.post("/api/trades", json={**closed, "pnl": -120.0, "account_id": prop["id"]})
    client.post("/api/trades", json={**closed, "pnl": 80.0, "account_id": prop["id"]})
    client.post("/api/trades", json={**trade_payload(), "account_id": prop["id"]})  # open
    return main_id, prop["id"]


def test_analytics_journal_calendar_reviews_and_positions_follow_the_account_shown(client):
    main_id, prop_id = setup_two_accounts(client)
    everything = client.get("/api/analytics").json()
    assert everything["summary"]["trades"] == 3 and everything["summary"]["net_pnl"] == 260.0
    assert everything["open_trades"] == 1

    client.put("/api/accounts/active", json={"account_id": prop_id})
    prop = client.get("/api/analytics").json()
    assert prop["summary"]["trades"] == 2 and prop["summary"]["net_pnl"] == -40.0 and prop["open_trades"] == 1
    assert prop["equity_curve"][0]["equity"] == 100_000.0
    assert client.get("/api/trades").json()["total"] == 3
    assert len(client.get("/api/positions").json()["positions"]) == 1
    month = client.get("/api/analytics/calendar").json()
    assert round(sum(day["pnl"] for day in month["days"] if day["pnl"]), 2) == -40.0
    assert client.get("/api/reviews", params={"days": 3}).json()[1]["pnl"] == -40.0

    # An explicit ?account= wins over the account shown; "all" shows everything.
    assert client.get("/api/analytics", params={"account": main_id}).json()["summary"]["net_pnl"] == 300.0
    assert client.get("/api/trades", params={"account": "all"}).json()["total"] == 4
    assert client.get("/api/positions", params={"account": main_id}).json()["positions"] == []
    assert client.get("/api/analytics", params={"account": 999}).status_code == 404
    assert client.get("/api/analytics", params={"account": "nope"}).status_code == 422


def test_reports_and_sizing_use_the_account_shown(client):
    main_id, prop_id = setup_two_accounts(client)
    client.put("/api/accounts/active", json={"account_id": prop_id})
    market = next(m for m in client.get("/api/markets").json() if m["symbol"] == "XAUUSD")
    sized = client.post(
        "/api/risk/position-size", json={"market_id": market["id"], "entry_price": 2650.0, "stop_price": 2640.0}
    ).json()
    assert sized["account_equity"] == 99_960.0  # the challenge account, not the main one
    pdf = client.get("/api/reports/performance.pdf")
    assert pdf.status_code == 200 and pdf.content.startswith(b"%PDF")


def test_a_prop_challenge_follows_its_own_trading_account(client):
    update_settings(client, max_daily_loss_pct=50, max_risk_pct=10, warn_outside_sessions=False)
    complete_checklist(client)
    main_id = client.get("/api/settings").json()["default_account_id"]
    funded = new_account(client, starting_balance=10_000.0)
    created = challenge(client, account_id=funded["id"], min_trading_days=0)
    assert created["account_id"] == funded["id"]
    closed_trade(client, -2, 900.0)  # on the main account: not the challenge's business
    assert client.get(f"/api/prop/challenges/{created['id']}").json()["metrics"]["profit"] == 0.0

    on_challenge = trade_payload(status="closed", entry_time=(now_utc() - timedelta(days=1)).isoformat())
    client.post("/api/trades", json={**on_challenge, "exit_price": 2640.0, "pnl": -600.0, "account_id": funded["id"]})
    metrics = client.get(f"/api/prop/challenges/{created['id']}").json()["metrics"]
    assert metrics["profit"] == -600.0

    bad = client.put(f"/api/prop/challenges/{created['id']}", json={**created, "account_id": 999})
    assert bad.status_code == 422
    refused = client.post("/api/prop/challenges", json={**created, "name": "x", "account_id": 999})
    assert refused.status_code == 422
    # The main account keeps trading while the challenge account would breach its daily loss rule.
    assert client.post("/api/trades", json={**trade_payload(), "account_id": main_id}).status_code == 201


def test_alert_tickets_are_sized_on_the_rules_account(client):
    prop = new_account(client, starting_balance=100_000.0)
    factory = client.app.state.session_factory
    with factory() as db:
        settings = get_settings(db)
        default_rule = make_rule(db, conditions=[SWEEP, PIN])
        prop_rule = make_rule(db, name="On the challenge", conditions=[SWEEP, PIN], account_id=prop["id"])
        now = FIXED_LAST_OPEN + timedelta(minutes=16)
        small = alerts.evaluate(db, settings, default_rule, "XAUUSD", series(), now)
        large = alerts.evaluate(db, settings, prop_rule, "XAUUSD", series(), now)
        assert small.found and large.found and small.account_id != large.account_id == prop["id"]
        assert large.risk_amount > 9 * small.risk_amount  # 1% of 100k vs 1% of 10k (lots rounded down)
        precheck = alerts.guardrail_precheck(db, settings, large, now)
        assert "RISK_ABOVE_MAX" not in {v["code"] for v in precheck}  # judged on the challenge's equity
    rule = client.get(f"/api/alerts/rules/{prop_rule.id}").json()
    assert rule["account_id"] == prop["id"]
    body = {**rule, "account_id": 999}
    assert client.put(f"/api/alerts/rules/{prop_rule.id}", json=body).status_code == 422


def test_risk_notifications_name_each_account(client, http):  # noqa: F811
    main_id, prop_id = setup_two_accounts(client)
    add_channel(client, "ntfy")
    enable(client)
    today = now_utc().replace(microsecond=0)
    for account_id, pnl in ((main_id, -400.0), (prop_id, -3_500.0)):  # both beyond their 3% daily limit
        loss = trade_payload(status="closed", entry_time=(today - timedelta(minutes=5)).isoformat(), exit_price=2600.0)
        assert client.post("/api/trades", json={**loss, "pnl": pnl, "account_id": account_id}).status_code == 201
    client.put("/api/accounts/active", json={"account_id": main_id})  # what the app shows does not matter
    scheduler = client.app.state.scheduler
    scheduler.run_now("risk-watch")
    scheduler.run_now("risk-watch")
    limits = [row for row in risk_log(client) if row["data"]["code"] == "DAILY_LOSS_LIMIT"]
    assert sorted(row["title"] for row in limits) == [
        "FTMO Challenge 1: Daily loss limit reached",
        "Main Account: Daily loss limit reached",
    ]
    with factory_session(client) as db:
        assert db.scalar(select(Trade.account_id).where(Trade.pnl == -3_500.0)) == prop_id


def factory_session(client):
    return client.app.state.session_factory()
