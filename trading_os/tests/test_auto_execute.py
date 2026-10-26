"""Strategy bindings and guarded auto-execution (v1.7).

Every auto order goes through the one execution path, so the guardrails, the typed-name gate on real accounts,
the per-binding risk/lot/day caps and idempotency all hold. These tests drive the service directly (a fabricated
alert) and the bindings API.
"""

from sqlalchemy import select

from trading_os.models import Execution, StrategyAlert, StrategyBinding
from trading_os.services import auto_execute
from trading_os.services.account import get_settings, local_now

from .conftest import complete_checklist, update_settings
from .test_execution import bridge_account, connect, new_account, paper_account, ready, switch


def strategy(client, name="Auto SMC") -> dict:
    return client.post("/api/backtest/strategies", json={"name": name}).json()


def bind(client, strategy_id, account_id, **fields) -> dict:
    body = {"account_id": account_id, "risk_pct": 1.0, **fields}
    response = client.put(f"/api/bindings/strategy/{strategy_id}", json=body)
    assert response.status_code == 200, response.text
    return response.json()


def arm(client, binding_id, on=True, confirm=""):
    return client.post(f"/api/bindings/{binding_id}/arm", json={"auto_execute": on, "confirm": confirm})


def make_alert(client, name="Auto SMC", **over):
    factory = client.app.state.session_factory
    with factory() as db:
        settings = get_settings(db)
        fields = dict(
            created_at=local_now(settings),
            candle_time=local_now(settings),
            strategy=name,
            symbol="XAUUSD",
            timeframe="M15",
            direction="long",
            entry_price=2650.0,
            stop_loss=2645.0,
            take_profits=[2665.0],
            planned_rr=3.0,
            confluences=["Liquidity sweep"],
            details={},
            status="new",
        )
        fields.update(over)
        alert = StrategyAlert(**fields)
        db.add(alert)
        db.commit()
        return alert.id


def run_auto(client, alert_id, binding_id):
    factory = client.app.state.session_factory
    with factory() as db:
        settings = get_settings(db)
        alert = db.get(StrategyAlert, alert_id)
        binding = db.get(StrategyBinding, binding_id)
        return auto_execute.execute_for_alert(db, settings, client.app.state.secrets, alert, binding)


def executions(client):
    with client.app.state.session_factory() as db:
        return list(db.scalars(select(Execution).order_by(Execution.id)))


# ------------------------------------------------------------------------------- API


def test_binding_is_created_and_read_back(client):
    ready(client)
    acct = paper_account(client, name="Paper", kind="demo")
    strat = strategy(client)
    binding = bind(client, strat["id"], acct["id"], risk_pct=1.5, max_lots_per_trade=2.0, max_trades_per_day=5)
    assert binding["auto_execute"] is False and binding["risk_pct"] == 1.5 and binding["can_arm"] is True
    assert binding["account_paper"] is True and binding["max_trades_per_day"] == 5
    assert client.get(f"/api/bindings/strategy/{strat['id']}").json()["binding"]["id"] == binding["id"]
    assert [b["id"] for b in client.get("/api/bindings").json()] == [binding["id"]]


def test_one_account_per_strategy_and_moving_it_disarms(client):
    ready(client)
    paper = paper_account(client, name="Paper", kind="demo")
    other = paper_account(client, name="Paper 2", kind="demo")
    strat = strategy(client)
    binding = bind(client, strat["id"], paper["id"])
    assert arm(client, binding["id"]).json()["auto_execute"] is True
    moved = bind(client, strat["id"], other["id"])  # same strategy, new account
    assert moved["id"] == binding["id"] and moved["account_id"] == other["id"] and moved["auto_execute"] is False


def test_binding_limits_are_validated(client):
    ready(client)
    acct = paper_account(client, name="Paper", kind="demo")
    strat = strategy(client)
    for bad in ({"account_id": acct["id"], "risk_pct": 0}, {"account_id": acct["id"], "risk_pct": 50},
                {"account_id": acct["id"], "max_trades_per_day": 999}):  # fmt: skip
        assert client.put(f"/api/bindings/strategy/{strat['id']}", json=bad).status_code == 422
    assert client.put(f"/api/bindings/strategy/{strat['id']}", json={"account_id": 99999}).status_code == 404


def test_a_real_account_needs_the_typed_name_to_arm(client):
    ready(client)
    acct, _token = bridge_account(client)  # a live MT5 bridge account ("MT5 Live") with sending switched on
    strat = strategy(client)
    binding = bind(client, strat["id"], acct["id"])
    wrong = arm(client, binding["id"], confirm="mt5 live")
    assert wrong.status_code == 422 and "Type the account name" in wrong.json()["detail"]
    assert client.get(f"/api/bindings/strategy/{strat['id']}").json()["binding"]["auto_execute"] is False
    assert arm(client, binding["id"], confirm="MT5 Live").json()["auto_execute"] is True
    assert arm(client, binding["id"], on=False).json()["auto_execute"] is False  # off is always allowed


def test_arming_needs_order_sending_switched_on_first(client):
    ready(client)
    acct = new_account(client, name="Paper", kind="demo")
    connect(client, acct["id"], "paper")  # connected but sending not switched on
    strat = strategy(client)
    binding = bind(client, strat["id"], acct["id"])
    assert binding["can_arm"] is False and "Switch order sending on" in binding["arming_problem"]
    refused = arm(client, binding["id"])
    assert refused.status_code == 409 and "Switch order sending on" in refused.json()["detail"]


def test_deleting_a_binding(client):
    ready(client)
    acct = paper_account(client, name="Paper", kind="demo")
    strat = strategy(client)
    binding = bind(client, strat["id"], acct["id"])
    assert client.delete(f"/api/bindings/{binding['id']}").status_code == 204
    assert client.get(f"/api/bindings/strategy/{strat['id']}").json()["binding"] is None


# --------------------------------------------------------------------------- execution


def armed_paper(client, **binding_fields):
    ready(client)
    acct = paper_account(client, name="Paper", kind="demo")
    strat = strategy(client)
    binding = bind(client, strat["id"], acct["id"], **binding_fields)
    assert arm(client, binding["id"]).json()["auto_execute"] is True
    return acct, strat, binding


def test_an_armed_binding_sends_the_order_and_books_the_trade(client):
    acct, strat, binding = armed_paper(client, risk_pct=1.0)
    alert_id = make_alert(client)
    result = run_auto(client, alert_id, binding["id"])
    assert result.status == "executed" and result.trade_id is not None
    [row] = executions(client)
    assert row.binding_id == binding["id"] and row.status == "filled" and row.account_id == acct["id"]
    with client.app.state.session_factory() as db:
        assert db.get(StrategyAlert, alert_id).status == "taken"
        binding_row = db.get(StrategyBinding, binding["id"])
        assert binding_row.last_auto_at is not None and "Simulated fill" in binding_row.last_result
    api_row = client.get("/api/executions").json()["items"][0]
    assert api_row["binding_id"] == binding["id"]


def test_the_same_alert_never_sends_two_orders(client):
    _, _, binding = armed_paper(client)
    alert_id = make_alert(client)
    first = run_auto(client, alert_id, binding["id"])
    again = run_auto(client, alert_id, binding["id"])
    assert again.execution_id == first.execution_id and len(executions(client)) == 1


def test_risk_sizes_the_order_on_the_binding(client):
    _, strat, binding = armed_paper(client, risk_pct=0.5)
    small = run_auto(client, make_alert(client), binding["id"])
    bind(client, strat["id"], binding["account_id"], risk_pct=2.0)
    arm(client, binding["id"])
    big = run_auto(client, make_alert(client), binding["id"])
    rows = executions(client)
    assert rows[1].lots > rows[0].lots  # 2% risks more than 0.5%
    assert small.status == big.status == "executed"


def test_the_lot_cap_limits_the_order(client):
    _, _, binding = armed_paper(client, risk_pct=5.0, max_lots_per_trade=0.1)
    run_auto(client, make_alert(client), binding["id"])
    assert executions(client)[0].lots == 0.1


def test_the_daily_cap_stops_further_orders(client):
    _, _, binding = armed_paper(client, max_trades_per_day=2)
    statuses = [run_auto(client, make_alert(client), binding["id"]).status for _ in range(3)]
    assert statuses == ["executed", "executed", "skipped"]
    assert len(executions(client)) == 2


def test_an_unarmed_or_disabled_binding_sends_nothing(client):
    ready(client)
    acct = paper_account(client, name="Paper", kind="demo")
    strat = strategy(client)
    binding = bind(client, strat["id"], acct["id"])  # never armed
    assert run_auto(client, make_alert(client), binding["id"]).status == "skipped"
    assert executions(client) == []


def test_the_checklist_gate_blocks_auto_orders(client):
    # Armed, but the pre-market checklist is not done today.
    acct = paper_account(client, name="Paper", kind="demo")
    update_settings(client, warn_outside_sessions=False)
    strat = strategy(client)
    binding = bind(client, strat["id"], acct["id"], require_checklist=True)
    arm(client, binding["id"])
    result = run_auto(client, make_alert(client), binding["id"])
    assert result.status == "skipped" and "checklist" in result.message.lower()
    complete_checklist(client)
    assert run_auto(client, make_alert(client), binding["id"]).status == "executed"


def test_the_guardrails_still_block_auto_orders(client):
    acct, _, binding = armed_paper(client)
    update_settings(client, max_trades_per_day=1)
    # Use up the day's one trade on the binding's own account (the trade-count limit is per account).
    from .conftest import trade_payload

    assert client.post("/api/trades", json={**trade_payload(), "account_id": acct["id"]}).status_code == 201
    result = run_auto(client, make_alert(client), binding["id"])
    assert result.status == "blocked" and "MAX_TRADES_REACHED" in result.message
    assert all(row.binding_id != binding["id"] for row in executions(client))  # nothing sent


def test_switching_order_sending_off_stops_automation(client):
    acct, _, binding = armed_paper(client)
    assert switch(client, acct["id"], enabled=False).status_code == 200
    result = run_auto(client, make_alert(client), binding["id"])
    assert result.status == "skipped" and executions(client) == []


def test_the_scanner_hook_runs_only_armed_bindings(client):
    acct, _strat, binding = armed_paper(client)
    strategy(client, name="Not bound")  # exists but has no binding
    alert_bound = make_alert(client, name="Auto SMC")
    alert_free = make_alert(client, name="Not bound")
    results_rows = [
        {"status": "alert", "alert_id": alert_bound},
        {"status": "alert", "alert_id": alert_free},
        {"status": "ok", "alert_id": None},
    ]
    with client.app.state.session_factory() as db:
        settings = get_settings(db)
        out = auto_execute.run_for_results(db, settings, client.app.state.secrets, results_rows)
    assert [o["status"] for o in out] == ["executed"]
    assert out[0]["symbol"] == "XAUUSD" and len(executions(client)) == 1


def test_without_a_secret_box_nothing_is_sent(client):
    _, _, binding = armed_paper(client)
    with client.app.state.session_factory() as db:
        settings = get_settings(db)
        alert = db.get(StrategyAlert, make_alert(client))
        result = auto_execute.execute_for_alert(db, settings, None, alert, db.get(StrategyBinding, binding["id"]))
    assert result.status == "skipped" and executions(client) == []


def test_bindings_are_in_backups(client):
    import json

    from trading_os.services.backup import SCHEMA_VERSION

    _, strat, binding = armed_paper(client)
    backup = client.get("/api/export/backup.json").json()
    assert backup["schema_version"] == SCHEMA_VERSION
    [row] = backup["tables"]["strategy_bindings"]
    assert row["auto_execute"] is True and row["risk_pct"] == 1.0
    client.delete(f"/api/bindings/{binding['id']}")
    restored = client.post("/api/import", files={"file": ("b.json", json.dumps(backup), "application/json")})
    assert restored.status_code == 200, restored.text
    assert client.get(f"/api/bindings/strategy/{strat['id']}").json()["binding"]["auto_execute"] is True


def test_the_background_scanner_job_auto_executes(client, monkeypatch):
    # The real alert-scanner job (register_jobs closure) must call auto_execute with the scheduler's secret box.
    from trading_os.services import alerts

    acct, _strat, binding = armed_paper(client)
    client.put("/api/alerts/scanner", json={"enabled": True, "interval_sec": 60})
    alert_id = make_alert(client)
    # Stand in for a real scan that found one setup for the bound strategy.
    monkeypatch.setattr(
        alerts,
        "scan",
        lambda db, settings, now, rule_ids=None: [{"status": "alert", "alert_id": alert_id, "symbol": "XAUUSD"}],
    )
    summary = client.app.state.scheduler.run_now("alert-scanner").last_result
    assert summary and summary.get("auto") and summary["auto"][0]["status"] == "executed"
    rows = executions(client)
    assert len(rows) == 1 and rows[0].binding_id == binding["id"]
