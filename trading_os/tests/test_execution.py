"""Live signal execution (v1.5): order bridges, the execution service and API, the MetaTrader EA protocol and the
safety rails (idempotency, typed confirmation, guardrails before anything is sent, no secret at rest)."""

import ast
import hashlib
import hmac
import json
import sys
import types
from datetime import timedelta
from pathlib import Path
from uuid import uuid4

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from trading_os.models import Execution, Trade, TradingAccount
from trading_os.services import bridges
from trading_os.services.bridges import base as bridge_base
from trading_os.services.bridges.oanda import OandaBridge
from trading_os.services.bridges.paper import PaperBridge

from .conftest import complete_checklist, trade_payload, update_settings

LOCAL = ("127.0.0.1", 50000)
LAN_PEER = ("192.168.1.50", 50000)
PASSWORD = "correct horse battery"
WEBHOOK_SECRET = "whsec-very-secret-value-123"
OANDA_TOKEN = "oanda-api-token-abcdef0123456789"
MT5_PASSWORD = "Mt5-Pa55word!xyz"


# --------------------------------------------------------------------------- helpers


def local(client) -> TestClient:
    """A client on the computer running Trading OS (the default test client is "another device")."""
    return TestClient(client.app, base_url="http://localhost:8765", client=LOCAL)


def ready(client) -> None:
    complete_checklist(client)
    update_settings(client, warn_outside_sessions=False)


def new_account(client, **fields) -> dict:
    body = {"name": "Lab Demo", "kind": "demo", "platform": "mt5", "login": "51234567", "server": "Broker-Demo"}
    response = client.post("/api/accounts", json={**body, **fields})
    assert response.status_code == 201, response.text
    return response.json()


def connect(client, account_id, connection, config=None, max_lots=None) -> dict:
    body = {"connection": connection, "connection_config": config or {}, "max_lots_per_order": max_lots}
    response = client.put(f"/api/accounts/{account_id}/connection", json=body)
    assert response.status_code == 200, response.text
    return response.json()


def switch(client, account_id, enabled=True, confirm=""):
    return client.put(f"/api/accounts/{account_id}/execution", json={"enabled": enabled, "confirm": confirm})


def send(client, account_id, key=None, order_type="market", **trade):
    body = {
        "account_id": account_id,
        "idempotency_key": key or uuid4().hex,
        "order_type": order_type,
        "trade": trade_payload(**trade),
    }
    return client.post("/api/execution/orders", json=body)


def paper_account(client, **fields) -> dict:
    acct = new_account(client, **fields)
    connect(client, acct["id"], "paper")
    assert switch(client, acct["id"]).status_code == 200
    return acct


def bridge_account(client, **config) -> tuple[dict, str]:
    acct = new_account(client, name="MT5 Live", kind="live")
    connect(client, acct["id"], "mt_bridge", config)
    token = local(client).post(f"/api/accounts/{acct['id']}/bridge-token").json()["token"]
    assert switch(client, acct["id"], confirm="MT5 Live").status_code == 200
    return acct, token


def executions(client) -> list[Execution]:
    with client.app.state.session_factory() as db:
        return list(db.scalars(select(Execution).order_by(Execution.id)))


def trades_on(client, account_id) -> list[Trade]:
    with client.app.state.session_factory() as db:
        return list(db.scalars(select(Trade).where(Trade.account_id == account_id)))


def fake_response(status, payload=None, method="POST", url="https://bridge.example.com/hook"):
    return httpx.Response(status, json=payload if payload is not None else {}, request=httpx.Request(method, url))


# --------------------------------------------------------------------------- paper, idempotency, rails


def test_a_paper_fill_logs_a_linked_journal_trade_on_that_account(client):
    ready(client)
    acct = paper_account(client)
    response = send(client, acct["id"])
    assert response.status_code == 201, response.text
    body = response.json()
    execution, trade = body["execution"], body["trade"]
    assert execution["status"] == "filled" and execution["fill_price"] == 2650.0 and execution["bridge"] == "paper"
    assert execution["broker_ref"] == f"PAPER-{execution['id']}" and execution["trade_id"] == trade["id"]
    assert trade["account_id"] == acct["id"] and trade["source"] == "signal" and trade["entry_price"] == 2650.0
    assert body["duplicate"] is False and body["account"]["name"] == "Lab Demo"

    log = client.get("/api/executions", params={"account_id": acct["id"]}).json()["items"]
    assert [row["id"] for row in log] == [execution["id"]]
    assert client.get("/api/executions", params={"trade_id": trade["id"]}).json()["items"][0]["symbol"] == "XAUUSD"
    main = client.get("/api/accounts").json()["default_account_id"]
    assert trades_on(client, main) == [] and len(trades_on(client, acct["id"])) == 1


def test_the_same_order_key_never_sends_twice(client, monkeypatch):
    ready(client)
    acct = paper_account(client)
    calls = []
    original = PaperBridge.submit

    def counting(self, ctx, order):
        calls.append(order.id)
        return original(self, ctx, order)

    monkeypatch.setattr(PaperBridge, "submit", counting)
    first = send(client, acct["id"], key="ticket-0001")
    again = send(client, acct["id"], key="ticket-0001")
    assert first.status_code == 201 and again.status_code == 200
    assert again.json()["duplicate"] is True
    assert again.json()["execution"]["id"] == first.json()["execution"]["id"]
    assert again.json()["trade"]["id"] == first.json()["trade"]["id"]
    assert len(calls) == 1 and len(executions(client)) == 1 and len(trades_on(client, acct["id"])) == 1

    other = paper_account(client, name="Second Paper")
    reused = send(client, other["id"], key="ticket-0001")
    assert reused.status_code == 409 and reused.json()["code"] == "key_reused"


def test_a_blocking_guardrail_sends_nothing(client, monkeypatch):
    update_settings(client, warn_outside_sessions=False)  # the pre-market checklist is NOT done: a block
    acct = paper_account(client)

    def never(self, ctx, order):  # pragma: no cover - must not run
        raise AssertionError("the bridge was called despite a blocking guardrail")

    monkeypatch.setattr(PaperBridge, "submit", never)
    response = send(client, acct["id"], key="blocked-0001")
    assert response.status_code == 423
    assert any(v["code"] == "CHECKLIST_INCOMPLETE" and v["severity"] == "block" for v in response.json()["violations"])
    assert executions(client) == [] and trades_on(client, acct["id"]) == []

    complete_checklist(client)  # the kill switch blocks too; the blocked key was not used up
    assert client.post("/api/killswitch", json={"hours": 2}).status_code == 200
    assert send(client, acct["id"], key="blocked-0001").status_code == 423
    assert executions(client) == []


def test_orders_need_a_connected_account_with_sending_switched_on(client):
    ready(client)
    acct = new_account(client)
    refused = send(client, acct["id"])
    assert refused.status_code == 409 and refused.json()["code"] == "not_connected"
    connect(client, acct["id"], "paper")
    off = send(client, acct["id"])
    assert off.status_code == 409 and off.json()["code"] == "execution_disabled"
    assert "turn it on" in off.json()["detail"]
    assert executions(client) == []

    switch(client, acct["id"])
    body = {**new_account(client, name="Old one"), "status": "archived"}
    archived = client.put(f"/api/accounts/{body['id']}", json={k: body[k] for k in ("name", "kind", "status")})
    assert archived.status_code == 200
    assert switch(client, body["id"]).status_code == 409


def test_max_lots_per_order(client):
    ready(client)
    acct = new_account(client)
    connect(client, acct["id"], "paper", max_lots=0.1)
    switch(client, acct["id"])
    too_big = send(client, acct["id"], position_size=0.2)
    assert too_big.status_code == 422 and too_big.json()["code"] == "max_lots"
    assert send(client, acct["id"], position_size=0.1).status_code == 201


def test_live_connections_need_the_account_name_typed(client):
    acct = new_account(client, name="FTMO 100k")
    connect(client, acct["id"], "webhook", {"url": "https://bridge.example.com/hook"})
    missing = switch(client, acct["id"])
    assert missing.status_code == 422 and missing.json()["code"] == "confirm_name"
    assert switch(client, acct["id"], confirm="ftmo 100k").status_code == 422
    on = switch(client, acct["id"], confirm="FTMO 100k")
    assert on.status_code == 200 and on.json()["execution_enabled"] is True
    off = switch(client, acct["id"], enabled=False)
    assert off.status_code == 200 and off.json()["execution_enabled"] is False

    # The default account's name is the settings' account name.
    main = client.get("/api/accounts").json()["default_account_id"]
    connect(client, main, "webhook", {"url": "https://bridge.example.com/hook"})
    assert switch(client, main, confirm="Main Account").json()["execution_enabled"] is True


def test_changing_the_connection_switches_sending_off(client):
    acct = paper_account(client)
    changed = connect(client, acct["id"], "webhook", {"url": "https://a.example.com/x"})
    assert changed["switched_off"] is True and changed["execution_enabled"] is False

    switch(client, acct["id"], confirm="Lab Demo")
    same = connect(client, acct["id"], "webhook", {"url": "https://a.example.com/x", "symbol_suffix": ".m"})
    assert same["switched_off"] is False and same["execution_enabled"] is True
    moved = connect(client, acct["id"], "webhook", {"url": "https://b.example.com/x"})
    assert moved["switched_off"] is True and moved["execution_enabled"] is False


def test_connection_settings_are_validated(client):
    acct = new_account(client)

    def put(connection, config):
        body = {"connection": connection, "connection_config": config}
        return client.put(f"/api/accounts/{acct['id']}/connection", json=body)

    assert put("webhook", {"url": "http://bridge.example.com/hook"}).status_code == 422  # plain http, internet
    assert put("webhook", {"url": "http://192.168.1.30:9000/hook"}).status_code == 200  # plain http on the LAN
    assert put("webhook", {"url": "http://localhost:9000/hook"}).status_code == 200
    assert put("webhook", {"url": "https://user:pw@bridge.example.com/"}).status_code == 422
    assert put("oanda", {"account_id": "101-004-1234567-001", "environment": "demo"}).status_code == 422
    assert put("paper", {"symbol_suffix": "bad suffix!"}).status_code == 422
    assert put("paper", {"symbol_map": {"XAUUSD": "GOLD|x"}}).status_code == 422
    saved = put("paper", {"symbol_map": {"xauusd": "GOLD"}, "symbol_suffix": ".r"}).json()
    assert saved["connection_config"] == {"symbol_suffix": ".r", "symbol_map": {"XAUUSD": "GOLD"}}


# --------------------------------------------------------------------------- webhook


def test_webhook_orders_are_signed_and_no_secret_is_stored(client, monkeypatch):
    ready(client)
    acct = new_account(client, name="Copier")
    connect(client, acct["id"], "webhook", {"url": "https://bridge.example.com/hook", "symbol_suffix": ".pro"})
    client.put(f"/api/accounts/{acct['id']}/credentials", json={"webhook_secret": WEBHOOK_SECRET})
    switch(client, acct["id"], confirm="Copier")
    sent = []

    def fake_post(url, body, headers):
        sent.append((url, body, headers))
        return fake_response(200, {"ticket": 778899, "fill_price": 2650.4, "token": "echoed-" + WEBHOOK_SECRET})

    monkeypatch.setattr(bridge_base, "_http_post", fake_post)
    response = send(client, acct["id"], key="hook-order-1")
    assert response.status_code == 201, response.text
    execution, trade = response.json()["execution"], response.json()["trade"]
    assert execution["status"] == "filled" and execution["fill_price"] == 2650.4
    assert execution["broker_ref"] == "778899" and execution["broker_symbol"] == "XAUUSD.pro"
    assert trade["entry_price"] == 2650.4 and trade["account_id"] == acct["id"]

    ((url, body, headers),) = sent
    expected = "sha256=" + hmac.new(WEBHOOK_SECRET.encode(), body, hashlib.sha256).hexdigest()
    assert url == "https://bridge.example.com/hook" and headers["X-TradingOS-Signature"] == expected
    assert headers["Idempotency-Key"] == "hook-order-1"
    payload = json.loads(body)
    assert payload["event"] == "order" and payload["account"]["name"] == "Copier"
    assert payload["order"]["side"] == "buy" and payload["order"]["broker_symbol"] == "XAUUSD.pro"
    assert payload["order"]["lots"] == 0.2 and payload["order"]["sl"] == 2645.0 and payload["order"]["tp"] == 2665.0

    (row,) = executions(client)
    stored = json.dumps(row.request) + json.dumps(row.response) + row.message
    assert WEBHOOK_SECRET not in stored and expected not in stored and "sha256=" not in stored
    assert WEBHOOK_SECRET not in client.get("/api/executions").text


def test_webhook_rejections_and_timeouts_log_no_trade(client, monkeypatch):
    ready(client)
    acct = new_account(client, name="Copier")
    connect(client, acct["id"], "webhook", {"url": "https://bridge.example.com/hook"})
    switch(client, acct["id"], confirm="Copier")
    monkeypatch.setattr(bridge_base, "_http_post", lambda *a: fake_response(400, {"message": "Market closed"}))
    refused = send(client, acct["id"]).json()["execution"]
    assert refused["status"] == "rejected" and "Market closed" in refused["message"] and refused["trade_id"] is None

    def timeout(*_args):
        raise httpx.ReadTimeout("slow")

    monkeypatch.setattr(bridge_base, "_http_post", timeout)
    lost = send(client, acct["id"]).json()["execution"]
    assert lost["status"] == "error" and "may or may not have arrived" in lost["message"]
    assert trades_on(client, acct["id"]) == []

    monkeypatch.setattr(bridge_base, "_http_post", lambda *a: fake_response(202, {}))
    accepted = send(client, acct["id"]).json()
    assert accepted["execution"]["status"] == "sent" and accepted["trade"]["entry_price"] == 2650.0
    tested = client.post(f"/api/accounts/{acct['id']}/test").json()
    assert tested["ok"] is True and "HTTP 202" in tested["message"]


# --------------------------------------------------------------------------- MetaTrader EA bridge


def test_the_mt_bridge_text_protocol(client):
    ready(client)
    acct, token = bridge_account(client, symbol_suffix=".m")
    queued = send(client, acct["id"]).json()
    execution, trade = queued["execution"], queued["trade"]
    assert execution["status"] == "pending" and trade["entry_price"] == 2650.0

    poll = client.get("/api/bridge/orders", params={"token": token})
    assert poll.status_code == 200 and poll.headers["content-type"].startswith("text/plain")
    assert poll.text == f"{execution['id']}|XAUUSD.m|buy|market|0.2|2650|2645|2665\n"
    assert client.get("/api/bridge/orders", headers={"X-Bridge-Token": token}).text == ""  # handed out once
    assert client.get("/api/executions").json()["items"][0]["status"] == "sent"

    report = client.post(
        f"/api/bridge/orders/{execution['id']}",
        params={"token": token},
        content="status=filled&ticket=5551234&price=2650.7",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    assert report.status_code == 200 and report.text.strip() == "OK"
    row = client.get("/api/executions").json()["items"][0]
    assert (row["status"], row["broker_ref"], row["fill_price"]) == ("filled", "5551234", 2650.7)
    filled = client.get(f"/api/trades/{trade['id']}").json()
    assert filled["entry_price"] == 2650.7 and filled["entry_time"] >= trade["entry_time"]
    again = client.post(f"/api/bridge/orders/{execution['id']}?token={token}", content="status=filled&price=2650.7")
    assert again.status_code == 200  # the same report twice is harmless

    # A rejected order: its untouched journal trade is removed, the Execution keeps the reason.
    second = send(client, acct["id"]).json()
    client.get("/api/bridge/orders", headers={"X-Bridge-Token": token})
    rejected = client.post(
        f"/api/bridge/orders/{second['execution']['id']}",
        headers={"X-Bridge-Token": token, "Content-Type": "text/plain"},
        content="status=rejected\nmessage=Not enough money (10019)",
    )
    assert rejected.status_code == 200
    row = client.get("/api/executions").json()["items"][0]
    assert row["status"] == "rejected" and row["trade_id"] is None and "Not enough money" in row["message"]
    assert row["response"]["removed_trade_id"] == second["trade"]["id"]
    assert client.get(f"/api/trades/{second['trade']['id']}").status_code == 404
    late = client.post(f"/api/bridge/orders/{second['execution']['id']}?token={token}", content="status=placed")
    assert late.status_code == 409


def test_unknown_bridge_tokens_get_401(client):
    acct, token = bridge_account(client)
    for path, method in (
        ("/api/bridge/orders", "get"),
        ("/api/bridge/orders/1", "post"),
        ("/api/bridge/account", "post"),
    ):
        assert getattr(client, method)(path, params={"token": "not-the-token-at-all"}).status_code == 401
        assert getattr(client, method)(path).status_code == 401
    other = new_account(client, name="Other")
    with client.app.state.session_factory() as db:
        assert db.get(TradingAccount, other["id"]).last_seen_at is None


def test_bridge_contact_and_account_figures_update_the_account(client):
    acct, token = bridge_account(client)
    panel = client.get(f"/api/accounts/{acct['id']}/connection").json()
    assert panel["last_seen_at"] is None and panel["polling"] is True and panel["ready"] is True
    tested = client.post(f"/api/accounts/{acct['id']}/test").json()
    assert tested["ok"] is False and "No contact" in tested["message"]

    figures = "balance=10500&equity=10620.5&margin=530&free_margin=10090.5&profit=120.5&positions=1"
    pushed = client.post(
        "/api/bridge/account", content=figures, headers={"X-Bridge-Token": token, "Content-Type": "text/plain"}
    )
    assert pushed.status_code == 200
    panel = client.get(f"/api/accounts/{acct['id']}/connection").json()
    assert panel["last_seen_seconds"] is not None and panel["snapshot"]["equity"] == 10620.5
    assert panel["snapshot"]["margin_level"] == pytest.approx(2003.87, abs=0.01)
    summary = client.get("/api/accounts/summary", params={"account": acct["id"]}).json()
    metrics = summary["accounts"][[a["id"] for a in summary["accounts"]].index(acct["id"])]["metrics"]
    assert metrics["source"] == "bridge" and metrics["equity"] == 10620.5 and metrics["open_positions"] == 1
    assert summary["totals"]["live"] is True and summary["totals"]["floating_pnl"] == 120.5
    assert client.post(f"/api/accounts/{acct['id']}/test").json()["ok"] is True


def test_bridge_endpoints_work_from_a_vps_when_a_remote_password_is_set(client):
    acct, token = bridge_account(client)
    assert local(client).put("/api/access/password", json={"password": PASSWORD}).status_code == 200
    vps = TestClient(client.app, base_url="http://192.168.1.20:8765", client=LAN_PEER)
    assert vps.get("/api/accounts").status_code == 401  # everything else needs a sign-in
    assert vps.get("/api/bridge/orders", headers={"X-Bridge-Token": token}).status_code == 200
    assert vps.get("/api/bridge/orders", headers={"X-Bridge-Token": "wrong-token-value"}).status_code == 401
    figures = "balance=1&equity=1&margin=0&free_margin=1&profit=0&positions=0"
    assert vps.post("/api/bridge/account", params={"token": token}, content=figures).status_code == 200
    assert vps.get(f"/api/accounts/{acct['id']}/bridge-token").status_code == 401


def test_the_bridge_token_is_shown_and_rotated_only_on_this_computer(client):
    acct = new_account(client)
    assert client.get(f"/api/accounts/{acct['id']}/bridge-token").json()["code"] == "local_only"
    assert client.post(f"/api/accounts/{acct['id']}/bridge-token").status_code == 403
    here = local(client)
    assert here.get(f"/api/accounts/{acct['id']}/bridge-token").json() == {"token": None}
    first = here.post(f"/api/accounts/{acct['id']}/bridge-token").json()
    assert first["credentials"]["bridge_token"]["set"] is True
    assert here.get(f"/api/accounts/{acct['id']}/bridge-token").json()["token"] == first["token"]
    second = here.post(f"/api/accounts/{acct['id']}/bridge-token").json()["token"]
    assert second != first["token"]
    listing = client.get("/api/accounts").text + client.get(f"/api/accounts/{acct['id']}/connection").text
    assert first["token"] not in listing and second not in listing
    assert client.get("/api/bridge/orders", params={"token": first["token"]}).status_code == 401
    assert client.get("/api/bridge/orders", params={"token": second}).status_code == 200


def test_pending_orders_cancel_and_expire(client):
    ready(client)
    acct, token = bridge_account(client)
    waiting = send(client, acct["id"]).json()
    cancelled = client.post(f"/api/executions/{waiting['execution']['id']}/cancel")
    assert cancelled.status_code == 200 and cancelled.json()["status"] == "cancelled"
    assert client.get(f"/api/trades/{waiting['trade']['id']}").status_code == 404
    assert client.get("/api/bridge/orders", params={"token": token}).text == ""

    delivered = send(client, acct["id"]).json()
    client.get("/api/bridge/orders", params={"token": token})
    refused = client.post(f"/api/executions/{delivered['execution']['id']}/cancel")
    assert refused.status_code == 409 and refused.json()["code"] == "delivered"

    stale = send(client, acct["id"]).json()
    with client.app.state.session_factory() as db:
        row = db.get(Execution, stale["execution"]["id"])
        row.created_at = row.created_at - timedelta(minutes=10)
        db.commit()
    assert client.get("/api/bridge/orders", params={"token": token}).text == ""  # never fired late
    expired = client.get("/api/executions").json()["items"][0]
    assert expired["status"] == "cancelled" and expired["message"].startswith("Expired")
    assert client.get(f"/api/trades/{stale['trade']['id']}").status_code == 404

    # Paper limit orders wait as pending and can be cancelled; switching sending off cancels queued MT orders.
    paper = paper_account(client, name="Paper Two")
    limit = send(client, paper["id"], order_type="limit").json()
    assert limit["execution"]["status"] == "pending" and limit["execution"]["cancellable"] is True
    assert client.post(f"/api/executions/{limit['execution']['id']}/cancel").json()["status"] == "cancelled"
    queued = send(client, acct["id"]).json()
    switch(client, acct["id"], enabled=False)
    rows = {row["id"]: row for row in client.get("/api/executions").json()["items"]}
    assert rows[queued["execution"]["id"]]["status"] == "cancelled"
    assert client.get(f"/api/trades/{queued['trade']['id']}").status_code == 404


def test_a_rejected_order_keeps_a_trade_the_trader_already_worked_on(client):
    ready(client)
    acct, token = bridge_account(client)
    queued = send(client, acct["id"]).json()
    trade = queued["trade"]
    closing = trade_payload(status="closed", exit_price=2660.0, account_id=acct["id"], entry_time=trade["entry_time"])
    assert client.put(f"/api/trades/{trade['id']}", json=closing).status_code == 200
    client.get("/api/bridge/orders", params={"token": token})
    client.post(
        f"/api/bridge/orders/{queued['execution']['id']}?token={token}", content="status=rejected&message=Off quotes"
    )
    kept = client.get(f"/api/trades/{trade['id']}").json()
    assert "order-rejected" in kept["tags"] and "Off quotes" in kept["notes"]
    assert client.get("/api/executions").json()["items"][0]["trade_id"] == trade["id"]


# --------------------------------------------------------------------------- broker APIs


def test_oanda_orders_units_and_symbol_mapping(client, monkeypatch):
    ready(client)
    acct = new_account(client, name="OANDA Practice", platform="api")
    connect(client, acct["id"], "oanda", {"account_id": "101-004-1234567-001", "environment": "practice"})
    assert switch(client, acct["id"], confirm="OANDA Practice").status_code == 409  # the token is missing
    client.put(f"/api/accounts/{acct['id']}/credentials", json={"api_token": OANDA_TOKEN})
    assert switch(client, acct["id"], confirm="OANDA Practice").status_code == 200
    calls = []

    def fake_request(method, url, headers, body=None):
        calls.append((method, url, headers, json.loads(body) if body else None))
        if url.endswith("/summary"):
            account = {"balance": "100000.0", "NAV": "100150.5", "unrealizedPL": "150.5", "marginUsed": "2650.0"}
            account.update(marginAvailable="97500.5", openTradeCount=1)
            return fake_response(200, {"account": account}, "GET", url)
        fill = {"id": "101", "price": "2650.25", "tradeOpened": {"tradeID": "101"}}
        return fake_response(201, {"orderCreateTransaction": {"id": "100"}, "orderFillTransaction": fill}, method, url)

    monkeypatch.setattr(bridge_base, "_http_request", fake_request)
    short = dict(direction="short", entry_price=2650.0, stop_loss=2655.0, take_profit=2635.0)
    result = send(client, acct["id"], **short).json()
    assert result["execution"]["status"] == "filled" and result["execution"]["fill_price"] == 2650.25
    assert result["execution"]["broker_ref"] == "101" and result["execution"]["broker_symbol"] == "XAU_USD"
    method, url, headers, body = calls[-1]
    assert method == "POST" and url == "https://api-fxpractice.oanda.com/v3/accounts/101-004-1234567-001/orders"
    assert headers["Authorization"] == f"Bearer {OANDA_TOKEN}"
    order = body["order"]
    assert order["type"] == "MARKET" and order["instrument"] == "XAU_USD" and order["units"] == "-20"
    assert order["stopLossOnFill"] == {"price": "2655.00"} and order["takeProfitOnFill"] == {"price": "2635.00"}

    euro = dict(symbol="EURUSD", entry_price=1.085, stop_loss=1.083, take_profit=1.091, position_size=0.1)
    send(client, acct["id"], order_type="limit", **euro)
    order = calls[-1][3]["order"]
    assert (order["instrument"], order["units"], order["type"], order["price"]) == (
        "EUR_USD",
        "10000",
        "LIMIT",
        "1.08500",
    )

    bridge = OandaBridge()
    assert bridge.broker_symbol("NAS100", {}) == "NAS100_USD"
    assert bridge.broker_symbol("XAUUSD", {"symbol_map": {"XAUUSD": "XAU_EUR"}}) == "XAU_EUR"
    tested = client.post(f"/api/accounts/{acct['id']}/test").json()
    assert (
        tested["ok"] is True and tested["snapshot"]["equity"] == 100150.5 and tested["snapshot"]["open_positions"] == 1
    )
    stored = "".join(json.dumps(r.request) + json.dumps(r.response) for r in executions(client))
    assert OANDA_TOKEN not in stored and "Bearer" not in stored


def fake_mt5(calls: list) -> types.ModuleType:
    module = types.ModuleType("MetaTrader5")
    module.TRADE_ACTION_DEAL, module.TRADE_ACTION_PENDING, module.TRADE_ACTION_REMOVE = 1, 5, 8
    module.ORDER_TYPE_BUY, module.ORDER_TYPE_SELL = 0, 1
    module.ORDER_FILLING_FOK, module.ORDER_FILLING_IOC, module.ORDER_FILLING_RETURN = 0, 1, 2
    module.ORDER_TIME_GTC = 0

    def initialize(**kwargs):
        calls.append(("initialize", kwargs))
        return True

    def order_send(request):
        calls.append(("order_send", request))
        return types.SimpleNamespace(retcode=10009, order=880011, deal=990022, price=2650.3, comment="done")

    module.initialize = initialize
    module.shutdown = lambda: calls.append(("shutdown", None))
    module.last_error = lambda: (1, "Success")
    module.symbol_select = lambda symbol, enable: symbol == "XAUUSD"
    module.symbol_info = lambda symbol: types.SimpleNamespace(filling_mode=2)
    module.symbol_info_tick = lambda symbol: types.SimpleNamespace(ask=2650.2, bid=2650.0)
    module.order_send = order_send
    module.account_info = lambda: types.SimpleNamespace(
        balance=50_000.0, equity=50_120.0, profit=120.0, margin=530.0, margin_free=49_590.0, margin_level=9456.6
    )
    module.positions_total = lambda: 1
    return module


def test_mt5_terminal_with_the_metatrader5_package(client, monkeypatch):
    ready(client)
    calls = []
    monkeypatch.setitem(sys.modules, "MetaTrader5", fake_mt5(calls))
    acct = new_account(client, name="MT5 Direct")
    connect(client, acct["id"], "mt5_terminal")
    assert switch(client, acct["id"], confirm="MT5 Direct").json()["code"] == "not_ready"  # no password yet
    client.put(f"/api/accounts/{acct['id']}/credentials", json={"password": MT5_PASSWORD})
    assert switch(client, acct["id"], confirm="MT5 Direct").status_code == 200

    result = send(client, acct["id"]).json()
    assert result["execution"]["status"] == "filled" and result["execution"]["fill_price"] == 2650.3
    assert result["execution"]["broker_ref"] == "880011" and result["trade"]["entry_price"] == 2650.3
    assert calls[0] == ("initialize", {"login": 51234567, "password": MT5_PASSWORD, "server": "Broker-Demo"})
    request = next(item for name, item in calls if name == "order_send")
    assert request["type"] == 0 and request["price"] == 2650.2 and request["volume"] == 0.2
    assert request["sl"] == 2645.0 and request["tp"] == 2665.0 and request["type_filling"] == 1
    assert calls[-1] == ("shutdown", None)
    tested = client.post(f"/api/accounts/{acct['id']}/test").json()
    assert tested["ok"] is True and tested["snapshot"]["equity"] == 50_120.0
    assert MT5_PASSWORD not in "".join(json.dumps(r.request) + json.dumps(r.response) for r in executions(client))


def test_mt5_terminal_without_the_package_says_so(client, monkeypatch):
    ready(client)
    monkeypatch.setitem(sys.modules, "MetaTrader5", None)  # import fails, as on macOS / Linux
    acct = new_account(client, name="MT5 Direct")
    connect(client, acct["id"], "mt5_terminal")
    client.put(f"/api/accounts/{acct['id']}/credentials", json={"password": MT5_PASSWORD})
    switch(client, acct["id"], confirm="MT5 Direct")
    tested = client.post(f"/api/accounts/{acct['id']}/test").json()
    assert tested["ok"] is False and "pip install MetaTrader5" in tested["message"]
    failed = send(client, acct["id"]).json()["execution"]
    assert failed["status"] == "error" and "only on Windows" in failed["message"]
    assert trades_on(client, acct["id"]) == []


# --------------------------------------------------------------------------- signals and alerts


def test_the_signal_preview_sizes_for_the_chosen_trading_account(client):
    ready(client)
    big = paper_account(client, name="Big Paper", starting_balance=100_000.0)
    body = {"symbol": "XAUUSD", "direction": "long", "entry": 2650, "stop_loss": 2645, "targets": [{"price": 2665}]}
    default = client.post("/api/signals/preview", json=body).json()
    assert default["sizing"]["lots"] == pytest.approx(0.2) and default["account"]["can_execute"] is False
    assert "Order sending is off" in default["account"]["reason"] or "Connect" in default["account"]["reason"]

    chosen = client.post("/api/signals/preview", json={**body, "account_id": big["id"]}).json()
    assert chosen["sizing"]["account_equity"] == 100_000.0 and chosen["sizing"]["lots"] == pytest.approx(2.0)
    assert chosen["ticket"]["account_id"] == big["id"] and chosen["account"]["can_execute"] is True
    assert chosen["account"]["name"] == "Big Paper" and chosen["account"]["order_type"] == "market"

    parsed = client.post(
        "/api/signals/parse", json={"text": "XAUUSD BUY LIMIT 2650 SL 2645 TP 2665", "account_id": big["id"]}
    )
    assert parsed.json()["draft"]["account_id"] == big["id"] and parsed.json()["draft"]["order_type"] == "limit"
    sent = client.post(
        "/api/execution/orders",
        json={"account_id": big["id"], "idempotency_key": uuid4().hex, "trade": chosen["ticket"]},
    )
    assert sent.status_code == 201 and sent.json()["trade"]["account_id"] == big["id"]


def test_strategy_alerts_and_background_jobs_never_send_orders():
    """Only POST /api/execution/orders calls the execution service: no job, alert or other module does.

    The one exception, api/accounts.py, may only cancel queued orders when an edit switches sending off.
    """
    package = Path(__file__).resolve().parent.parent
    importers = set()
    allowed_in_accounts = {"cancel_queued"}
    for path in package.rglob("*.py"):
        if "tests" in path.parts:
            continue
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if not isinstance(node, ast.ImportFrom):
                continue
            module = node.module or ""
            names = {alias.name for alias in node.names}
            if module.endswith("execution") or (module.endswith("services") and "execution" in names):
                where = path.relative_to(package).as_posix()
                if where == "api/accounts.py" and module.endswith("services.execution"):
                    assert names <= allowed_in_accounts, names
                    continue
                importers.add(where)
            elif node.level == 1 and not module and "execution" in names and path.parent.name == "services":
                importers.add(path.relative_to(package).as_posix())
    assert importers == {"api/execution.py"}, importers


def test_bridges_never_keep_secrets_in_what_they_store():
    secrets = ("tok-123456789",)
    cleaned = bridges.sanitize(
        {"token": "x", "Authorization": "Bearer y", "nested": {"api_token": "z", "note": "has tok-123456789"}, "ok": 1},
        secrets,
    )
    assert cleaned == {"nested": {"note": "has ••••"}, "ok": 1}
