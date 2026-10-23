"""Trading accounts (v1.5): the default account, several accounts, the account shown, per-account guardrails,
write-only encrypted credentials and backups."""

from datetime import timedelta

from sqlalchemy import select, text

from trading_os.models import AccountCredential, Trade, TradingAccount
from trading_os.services import account as account_service
from trading_os.services import accounts, backup, secrets_store

from .conftest import complete_checklist, now_utc, trade_payload, update_settings

PROP = {"name": "FTMO Challenge 1", "kind": "prop", "broker": "FTMO", "platform": "mt5", "login": "51234567"}


def new_account(client, **fields) -> dict:
    response = client.post("/api/accounts", json={**PROP, **fields})
    assert response.status_code == 201, response.text
    return response.json()


def accounts_by_name(client) -> dict:
    return {a["name"]: a for a in client.get("/api/accounts").json()["accounts"]}


def test_the_default_account_mirrors_the_settings(client):
    data = client.get("/api/accounts").json()
    (main,) = data["accounts"]
    assert main["is_default"] and main["name"] == "Main Account" and main["starting_balance"] == 10_000.0
    assert data["default_account_id"] == main["id"] and data["active_account_id"] is None
    assert set(data["enums"]["kinds"]) == {"live", "demo", "prop", "funded"}

    update_settings(client, account_name="Personal", starting_balance=25_000.0, currency="EUR")
    main = accounts_by_name(client)["Personal"]
    assert (main["starting_balance"], main["currency"]) == (25_000.0, "EUR")

    body = {**PROP, "name": "Personal live", "kind": "live", "currency": "EUR", "starting_balance": 30_000.0}
    assert client.put(f"/api/accounts/{main['id']}", json=body).status_code == 200
    settings = client.get("/api/settings").json()
    assert (settings["account_name"], settings["starting_balance"]) == ("Personal live", 30_000.0)
    assert client.get("/api/risk/status").json()["current_equity"] == 30_000.0


def test_trades_go_to_the_account_shown_and_each_account_has_its_own_equity(client):
    complete_checklist(client)
    update_settings(client, warn_outside_sessions=False)
    main = accounts_by_name(client)["Main Account"]
    prop = new_account(client, starting_balance=100_000.0)

    loss = trade_payload(status="closed", entry_time=(now_utc() - timedelta(days=2)).isoformat(), exit_price=2640.0)
    assert client.post("/api/trades", json={**loss, "pnl": -200.0}).json()["trade"]["account_id"] == main["id"]
    assert client.put("/api/accounts/active", json={"account_id": prop["id"]}).status_code == 200
    won = client.post("/api/trades", json={**loss, "pnl": 500.0}).json()["trade"]
    assert won["account_id"] == prop["id"]

    status = client.get("/api/risk/status").json()
    assert status["account_id"] == prop["id"] and status["current_equity"] == 100_500.0
    assert client.get("/api/settings").json()["active_account_id"] == prop["id"]
    summary = client.get("/api/accounts/summary").json()
    assert summary["scope"]["account_id"] == prop["id"] and summary["totals"]["balance"] == 100_500.0

    client.put("/api/accounts/active", json={"account_id": None})
    everything = client.get("/api/accounts/summary").json()
    assert everything["scope"]["account_id"] is None and everything["totals"]["balance"] == 110_300.0
    assert client.get("/api/risk/status").json()["current_equity"] == 110_300.0
    assert client.get("/api/accounts/summary", params={"account": main["id"]}).json()["totals"]["balance"] == 9_800.0


def test_accounts_in_another_currency_are_shown_on_their_own(client):
    eur = new_account(client, name="EUR broker", kind="live", currency="EUR", starting_balance=5_000.0)
    totals = client.get("/api/accounts/summary", params={"account": "all"}).json()
    assert totals["totals"]["balance"] == 10_000.0 and totals["scope"]["excluded"] == [eur["id"]]
    alone = client.get("/api/accounts/summary", params={"account": eur["id"]}).json()
    assert alone["scope"]["currency"] == "EUR" and alone["totals"]["balance"] == 5_000.0


def test_daily_loss_limits_are_per_account(client):
    complete_checklist(client)
    update_settings(client, warn_outside_sessions=False)
    prop = new_account(client)
    opened = client.post("/api/trades", json=trade_payload()).json()["trade"]
    closing = trade_payload(status="closed", exit_price=2630.0, pnl=-350.0, entry_time=opened["entry_time"])
    assert client.put(f"/api/trades/{opened['id']}", json=closing).status_code == 200  # main account: -3.5%

    assert client.post("/api/trades", json=trade_payload()).status_code == 423  # the main account is locked
    other = client.post("/api/trades", json={**trade_payload(), "account_id": prop["id"]})
    assert other.status_code == 201, other.text  # the challenge account has its own limit

    status = client.get("/api/risk/status").json()  # every account shown: the worst state, named
    assert status["state"] == "locked" and status["can_trade"] is False
    assert any(r["code"] == "DAILY_LOSS_LIMIT" and r["message"].startswith("Main Account: ") for r in status["reasons"])
    client.put("/api/accounts/active", json={"account_id": prop["id"]})
    assert client.get("/api/risk/status").json()["can_trade"] is True


def test_archived_and_unknown_accounts_take_no_new_trades(client):
    prop = new_account(client, status="archived")
    refused = client.post("/api/trades", json={**trade_payload(), "account_id": prop["id"]})
    assert refused.status_code == 422 and "archived" in refused.json()["detail"]
    unknown = client.post("/api/trades", json={**trade_payload(), "account_id": 999})
    assert unknown.status_code == 422 and "Unknown trading account" in unknown.json()["detail"]


def test_deleting_accounts_never_loses_journal_history(client):
    main = accounts_by_name(client)["Main Account"]
    assert client.delete(f"/api/accounts/{main['id']}").status_code == 409
    archived = client.put(f"/api/accounts/{main['id']}", json={**PROP, "name": "Main Account", "status": "archived"})
    assert archived.status_code == 409

    prop = new_account(client)
    complete_checklist(client)
    update_settings(client, warn_outside_sessions=False)
    client.post("/api/trades", json={**trade_payload(), "account_id": prop["id"]})
    in_use = client.delete(f"/api/accounts/{prop['id']}")
    assert in_use.status_code == 409 and "archive it instead" in in_use.json()["detail"]

    empty = new_account(client, name="Spare")
    client.put("/api/accounts/active", json={"account_id": empty["id"]})
    assert client.delete(f"/api/accounts/{empty['id']}").status_code == 204
    assert client.get("/api/settings").json()["active_account_id"] is None
    assert new_account(client, name="Spare")["name"] == "Spare"
    assert client.post("/api/accounts", json={**PROP, "name": "Spare"}).status_code == 409


def test_credentials_are_write_only_and_encrypted(client):
    prop = new_account(client)
    secret = "MetaTrader-Pa55word!"
    response = client.put(f"/api/accounts/{prop['id']}/credentials", json={"password": secret, "api_token": ""})
    assert response.json() == {"credentials": {"password": {"set": True, "hint": "ord!"}}}
    listing = client.get("/api/accounts").text + client.get("/api/plan").text + client.get("/api/accounts/summary").text
    assert secret not in listing and '"hint":"ord!"' in listing.replace(" ", "")

    factory = client.app.state.session_factory
    box = client.app.state.secrets
    with factory() as db:
        row = db.scalar(select(AccountCredential))
        assert secret not in row.ciphertext and secrets_store.get(db, box, prop["id"], "password") == secret
        assert secret not in backup.export_json_bytes(db).decode()
    assert box.path.exists() and box.path.name == "secret.key"

    client.put(f"/api/accounts/{prop['id']}/credentials", json={"password": ""})
    assert accounts_by_name(client)["FTMO Challenge 1"]["credentials"] == {}


def test_a_restore_keeps_order_routing_off_and_attaches_old_trades(client):
    complete_checklist(client)
    update_settings(client, warn_outside_sessions=False)
    client.post("/api/trades", json=trade_payload())
    factory = client.app.state.session_factory
    with factory() as db:
        payload = backup.export_data(db)
    assert all("execution_enabled" not in row for row in payload["tables"]["trading_accounts"])

    # A backup written before v1.5: no accounts, trades without one.
    old = {**payload, "schema_version": 4, "tables": {**payload["tables"]}}
    old["tables"].pop("trading_accounts")
    old["tables"]["trades"] = [{k: v for k, v in row.items() if k != "account_id"} for row in old["tables"]["trades"]]
    with factory() as db:
        backup.restore_data(db, old)
        settings = account_service.get_settings(db)
        main = db.get(TradingAccount, settings.default_account_id)
        assert main is not None and main.name == settings.account_name and not main.execution_enabled
        assert all(trade.account_id == main.id for trade in db.scalars(select(Trade)))


def test_older_databases_get_a_default_account_for_their_trades(client):
    complete_checklist(client)
    update_settings(client, warn_outside_sessions=False)
    client.post("/api/trades", json=trade_payload())
    factory = client.app.state.session_factory
    with factory() as db:
        db.execute(text("UPDATE trades SET account_id = NULL"))
        db.commit()
        settings = account_service.get_settings(db)
        accounts.ensure_default_account(db, settings)
        assert db.scalar(select(Trade.account_id)) == settings.default_account_id


def test_account_metrics_floating_pnl_margin_and_bridge_snapshots(client):
    complete_checklist(client)
    update_settings(client, warn_outside_sessions=False)
    trade = client.post("/api/trades", json=trade_payload()).json()["trade"]  # 0.2 lots, 1% risk
    factory = client.app.state.session_factory
    with factory() as db:
        row = db.get(Trade, trade["id"])
        row.last_price = 2655.0
        db.commit()
    main = client.get("/api/accounts/summary").json()["accounts"][0]["metrics"]
    # XAUUSD: 100 oz per lot, 0.2 lots x +5.00 -> +100 USD; margin = 0.2 x 100 x 2655 / 100 leverage.
    assert main["source"] == "journal" and main["floating_pnl"] == 100.0 and main["equity"] == 10_100.0
    assert main["margin"] == 531.0 and main["free_margin"] == 9569.0 and main["margin_level"] == 1902.1

    with factory() as db:
        settings = account_service.get_settings(db)
        account = db.get(TradingAccount, settings.default_account_id)
        account.snapshot = {"balance": 10_100.0, "equity": 10_350.0, "floating_pnl": 250.0, "margin": 2600.0}
        account.snapshot_at = account_service.local_now(settings)
        db.commit()
    live = client.get("/api/accounts/summary").json()
    assert live["totals"]["live"] is True and live["totals"]["equity"] == 10_350.0
    assert live["accounts"][0]["metrics"]["source"] == "bridge"


def test_editing_where_orders_go_or_archiving_switches_sending_off(client):
    prop = new_account(client, platform="mt5", login="1001", server="Broker-Demo")
    factory = client.app.state.session_factory
    with factory() as db:
        account = db.get(TradingAccount, prop["id"])
        account.connection, account.execution_enabled = "mt5_terminal", True
        db.commit()
    body = {**PROP, "login": "1001", "server": "Broker-Demo", "notes": "renamed notes only"}
    client.put(f"/api/accounts/{prop['id']}", json=body)
    assert accounts_by_name(client)["FTMO Challenge 1"]["execution_enabled"] is True
    client.put(f"/api/accounts/{prop['id']}", json={**body, "server": "Broker-Live"})
    assert accounts_by_name(client)["FTMO Challenge 1"]["execution_enabled"] is False

    with factory() as db:
        db.get(TradingAccount, prop["id"]).execution_enabled = True
        db.commit()
    client.put(f"/api/accounts/{prop['id']}", json={**body, "server": "Broker-Live", "status": "archived"})
    assert accounts_by_name(client)["FTMO Challenge 1"]["execution_enabled"] is False


def test_demo_accounts_are_not_added_to_the_real_money_totals(client):
    paper = new_account(client, name="Paper", kind="demo", starting_balance=50_000.0)
    totals = client.get("/api/accounts/summary", params={"account": "all"}).json()
    assert totals["totals"]["balance"] == 10_000.0 and totals["scope"]["excluded"] == [paper["id"]]
    assert client.get("/api/risk/status").json()["current_equity"] == 10_000.0
    alone = client.get("/api/accounts/summary", params={"account": paper["id"]}).json()
    assert alone["totals"]["balance"] == 50_000.0
