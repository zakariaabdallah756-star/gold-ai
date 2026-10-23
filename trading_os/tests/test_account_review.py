"""Multi-account fixes found in review: account currencies, moved trades, deleting linked accounts, risk
notifications for every account, the default account in "All accounts", account-name clashes."""

from datetime import timedelta

from trading_os.models import AlertRule, Trade

from .conftest import complete_checklist, now_utc, trade_payload, update_settings
from .test_accounts import accounts_by_name, new_account
from .test_notifications import add_channel, enable
from .test_notifications import http as http  # noqa: F401 - pytest fixture used below
from .test_notifications import risk_log
from .test_prop import challenge

EURUSD = dict(symbol="EURUSD", entry_price=1.10, stop_loss=1.095, take_profit=1.12, position_size=1.0)


def ready(client):
    complete_checklist(client)
    update_settings(client, warn_outside_sessions=False, max_risk_pct=10)


def test_trades_on_an_account_in_another_currency_are_valued_in_that_currency(client):
    ready(client)
    update_settings(client, max_daily_loss_pct=20)
    eur = new_account(client, name="EUR broker", kind="live", currency="EUR", starting_balance=10_000.0)
    closed = trade_payload(**EURUSD, status="closed", entry_time=(now_utc() - timedelta(days=1)).isoformat())
    trade = client.post("/api/trades", json={**closed, "exit_price": 1.11, "account_id": eur["id"]}).json()["trade"]
    assert trade["pnl"] == round(1000 / 1.11, 2)  # 1000 USD of profit, in EUR at the exit price
    opened = client.post("/api/trades", json={**trade_payload(**EURUSD), "account_id": eur["id"]}).json()["trade"]
    factory = client.app.state.session_factory
    with factory() as db:
        row = db.get(Trade, opened["id"])
        assert abs(row.remaining_risk - row.risk_amount) < 0.01  # both in EUR

    client.put("/api/accounts/active", json={"account_id": eur["id"]})
    market = next(m for m in client.get("/api/markets").json() if m["symbol"] == "XAUUSD")
    sized = client.post(
        "/api/risk/position-size",
        json={"market_id": market["id"], "entry_price": 2650, "stop_price": 2640, "fx_rate": 0.9},
    )
    assert sized.status_code == 200 and sized.json()["currency"] == "EUR"


def test_moving_a_trade_to_another_account_is_judged_there(client):
    ready(client)
    update_settings(client, max_trades_per_day=2)
    other = new_account(client, name="Account B")
    for _ in range(2):
        assert client.post("/api/trades", json={**trade_payload(), "account_id": other["id"]}).status_code == 201
    mine = client.post("/api/trades", json=trade_payload()).json()["trade"]  # on the main account
    moved = client.put(f"/api/trades/{mine['id']}", json={**trade_payload(), "account_id": other["id"]})
    assert moved.status_code == 423 and "MAX_TRADES_REACHED" in {v["code"] for v in moved.json()["violations"]}
    assert client.put(f"/api/trades/{mine['id']}", json={**trade_payload(), "notes": "kept"}).status_code == 200


def test_an_account_followed_by_a_challenge_cannot_be_deleted(client):
    funded = new_account(client, name="Funded")
    linked = challenge(client, account_id=funded["id"], activate=False)
    refused = client.delete(f"/api/accounts/{funded['id']}")
    assert refused.status_code == 409 and "follows this account" in refused.json()["detail"]
    client.put(f"/api/prop/challenges/{linked['id']}", json={**linked, "account_id": None})
    factory = client.app.state.session_factory
    with factory() as db:
        db.add(AlertRule(name="Sized there", symbols=["XAUUSD"], account_id=funded["id"]))
        db.commit()
    assert client.delete(f"/api/accounts/{funded['id']}").status_code == 204
    with factory() as db:
        assert db.query(AlertRule).one().account_id is None


def test_risk_notifications_cover_demo_accounts_too(client, http):  # noqa: F811
    ready(client)
    add_channel(client, "ntfy")
    enable(client)
    demo = new_account(client, name="Practice", kind="demo", starting_balance=10_000.0)
    loss = trade_payload(status="closed", entry_time=(now_utc() - timedelta(minutes=5)).isoformat(), exit_price=2600.0)
    assert client.post("/api/trades", json={**loss, "pnl": -400.0, "account_id": demo["id"]}).status_code == 201
    client.app.state.scheduler.run_now("risk-watch")
    titles = [row["title"] for row in risk_log(client) if row["data"]["code"] == "DAILY_LOSS_LIMIT"]
    assert titles == ["Practice: Daily loss limit reached"]


def test_a_demo_default_account_stays_in_all_accounts(client):
    ready(client)
    main = accounts_by_name(client)["Main Account"]
    body = {"name": "Main Account", "kind": "demo", "currency": "USD", "starting_balance": 10_000.0}
    assert client.put(f"/api/accounts/{main['id']}", json=body).status_code == 200
    new_account(client, name="Live", kind="live")
    client.post("/api/trades", json=trade_payload())  # lands on the default account
    assert client.get("/api/trades").json()["total"] == 1  # and "All accounts" shows it


def test_the_account_name_cannot_clash_with_another_account(client):
    new_account(client, name="FTMO")
    refused = client.put("/api/settings", json={**client.get("/api/settings").json(), "account_name": "FTMO"})
    assert refused.status_code == 409


def test_daily_limit_messages_use_the_accounts_currency(client):
    ready(client)
    eur = new_account(client, name="EUR broker", kind="live", currency="EUR", starting_balance=10_000.0)
    refused = client.post("/api/trades", json={**trade_payload(**EURUSD), "account_id": eur["id"]})
    assert refused.status_code == 423  # 454.55 EUR at risk, 300 EUR of daily budget
    assert "EUR" in refused.json()["detail"] and "USD" not in refused.json()["detail"]
