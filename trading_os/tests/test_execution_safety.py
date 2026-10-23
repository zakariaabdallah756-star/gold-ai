"""Order-execution safety fixes found in review: OANDA units, restores, ids, unconfirmed MetaTrader orders,
cancel destination, client risk, symbol validation, bridge body cap, partial fills and EA warnings."""

import json
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from trading_os.models import AccountCredential, Execution, Trade, TradingAccount
from trading_os.services import backup
from trading_os.services.bridges import base as bridge_base

from .test_execution import (
    OANDA_TOKEN,
    bridge_account,
    connect,
    fake_response,
    local,
    new_account,
    paper_account,
    ready,
    send,
    switch,
)


def oanda_account(client, monkeypatch, *, fill=True):
    acct = new_account(client, name="OANDA Practice", platform="api")
    connect(client, acct["id"], "oanda", {"account_id": "101-004-1234567-001", "environment": "practice"})
    client.put(f"/api/accounts/{acct['id']}/credentials", json={"api_token": OANDA_TOKEN})
    assert switch(client, acct["id"], confirm="OANDA Practice").status_code == 200
    calls = []

    def fake_request(method, url, headers, body=None):
        calls.append((method, url, json.loads(body) if body else None))
        if method == "PUT":
            return fake_response(200, {"orderCancelTransaction": {"id": "9"}}, method, url)
        created = {"orderCreateTransaction": {"id": "100"}}
        if fill:
            created["orderFillTransaction"] = {"id": "101", "price": "2650.0", "tradeOpened": {"tradeID": "101"}}
        return fake_response(201, created, method, url)

    monkeypatch.setattr(bridge_base, "_http_request", fake_request)
    return acct, calls


NAS = dict(symbol="NAS100", entry_price=18_000.0, stop_loss=17_990.0, take_profit=18_030.0)


def test_oanda_never_sends_more_units_than_the_approved_lots(client, monkeypatch):
    ready(client)
    acct, calls = oanda_account(client, monkeypatch)
    # NAS100 trades in whole units of 1: 1.5 lots would round to 2 (more risk than approved).
    refused = send(client, acct["id"], position_size=1.5, **NAS).json()
    assert refused["execution"]["status"] == "rejected" and "1% off" in refused["execution"]["message"]
    assert calls == [] and refused["trade"] is None
    exact = send(client, acct["id"], position_size=2.0, **NAS).json()
    assert exact["execution"]["status"] == "filled" and calls[-1][2]["order"]["units"] == "2"


def test_a_restore_never_routes_orders_for_another_account(client):
    ready(client)
    live = new_account(client, name="OANDA live", platform="api")
    connect(client, live["id"], "paper")
    assert switch(client, live["id"]).status_code == 200
    client.put(f"/api/accounts/{live['id']}/credentials", json={"api_token": "live-token-123456"})
    factory = client.app.state.session_factory
    with factory() as db:
        payload = backup.export_data(db)
    same = json.loads(json.dumps(payload))
    other = json.loads(json.dumps(payload))
    row = next(r for r in other["tables"]["trading_accounts"] if r["id"] == live["id"])
    row["name"] = "Old demo"  # another account came back under this id

    with factory() as db:
        backup.restore_data(db, same)
        kept = db.get(TradingAccount, live["id"])
        assert kept.connection == "paper" and kept.execution_enabled
        assert db.scalar(select(AccountCredential).where(AccountCredential.account_id == live["id"])) is not None
    with factory() as db:
        backup.restore_data(db, other)
        reset = db.get(TradingAccount, live["id"])
        assert reset.name == "Old demo" and reset.connection == "none" and not reset.execution_enabled
        assert db.scalar(select(AccountCredential).where(AccountCredential.account_id == live["id"])) is None


def test_account_and_order_ids_are_never_reused(client):
    ready(client)
    first = new_account(client, name="Spare one")
    assert client.delete(f"/api/accounts/{first['id']}").status_code == 204
    assert new_account(client, name="Spare two")["id"] > first["id"]
    acct = paper_account(client, name="Paper", kind="demo")
    sent = send(client, acct["id"]).json()["execution"]["id"]
    factory = client.app.state.session_factory
    with factory() as db:
        payload = backup.export_data(db)
        payload["tables"]["executions"] = []
        backup.restore_data(db, payload)
    assert send(client, acct["id"]).json()["execution"]["id"] > sent  # the EA's "last id" stays meaningful


def test_an_unreported_metatrader_order_can_be_released(client):
    ready(client)
    acct, token = bridge_account(client)
    order = send(client, acct["id"]).json()
    assert client.get("/api/bridge/orders", params={"token": token}).text.startswith(f"{order['execution']['id']}|")
    factory = client.app.state.session_factory
    with factory() as db:
        row = db.get(Execution, order["execution"]["id"])
        row.response = {**row.response, "delivered_utc": (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()}
        db.commit()
    assert client.get("/api/bridge/orders", params={"token": token}).text == ""  # never handed out twice
    row = client.get("/api/executions").json()["items"][0]
    assert row["status"] == "sent" and row["message"].startswith("No report from MetaTrader")
    released = client.post(f"/api/executions/{order['execution']['id']}/cancel")
    assert released.status_code == 200 and released.json()["status"] == "cancelled"
    assert client.get(f"/api/trades/{order['trade']['id']}").status_code == 404


def test_a_resting_order_is_only_cancelled_where_it_was_sent(client, monkeypatch):
    ready(client)
    acct, calls = oanda_account(client, monkeypatch, fill=False)
    pending = send(client, acct["id"], order_type="limit").json()
    assert pending["execution"]["status"] == "pending"
    connect(client, acct["id"], "oanda", {"account_id": "101-004-2222222-001", "environment": "practice"})
    refused = client.post(f"/api/executions/{pending['execution']['id']}/cancel")
    assert refused.status_code == 409 and refused.json()["code"] == "destination_changed"
    assert not any(method == "PUT" for method, _url, _body in calls)


def test_the_order_risk_comes_from_the_lots_not_from_the_client(client):
    ready(client)
    acct = paper_account(client, name="Paper", kind="demo")
    sneaky = send(client, acct["id"], position_size=5.0, stop_loss=2640.0, risk_amount=1.0)
    assert sneaky.status_code == 423  # 5 lots x 10 points: far above the risk cap
    assert all(row.status != "filled" for row in _executions(client))


def test_order_symbols_cannot_inject_protocol_lines(client):
    ready(client)
    acct, token = bridge_account(client)
    response = send(client, acct["id"], symbol="A\n999999|XAUUSD")
    assert response.status_code == 422
    assert client.get("/api/bridge/orders", params={"token": token}).text == ""


def test_bridge_bodies_are_capped_before_they_are_read(client):
    huge = client.post("/api/bridge/account", content=b"x", headers={"Content-Length": str(64 * 1024 * 1024)})
    assert huge.status_code == 413


def test_partial_fills_and_ea_warnings_reach_the_journal(client):
    ready(client)
    acct, token = bridge_account(client)
    order = send(client, acct["id"], position_size=0.2).json()
    client.get("/api/bridge/orders", params={"token": token})
    report = {"status": "filled", "ticket": "777", "price": "2650.5", "volume": "0.1", "message": "SL not set"}
    done = client.post(f"/api/bridge/orders/{order['execution']['id']}", data=report, headers={"X-Bridge-Token": token})
    assert done.status_code == 200, done.text
    row = client.get("/api/executions").json()["items"][0]
    assert "partially: 0.1 of 0.2 lots" in row["message"] and "SL not set" in row["message"]
    factory = client.app.state.session_factory
    with factory() as db:
        assert db.get(Trade, order["trade"]["id"]).position_size == 0.1
        assert db.get(TradingAccount, acct["id"]).last_error == "SL not set"
    assert local(client) is not None


def _executions(client):
    with client.app.state.session_factory() as db:
        return list(db.scalars(select(Execution)))
