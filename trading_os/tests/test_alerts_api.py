"""Strategy alerts REST API: meta, rules CRUD + validation, dry-run test, scanner, feed, pushed candles, keys."""

from datetime import timedelta

import pytest
from sqlalchemy import select

from trading_os.models import Candle, StrategyAlert
from trading_os.services import market_data

from .conftest import now_utc, trade_payload, update_settings


@pytest.fixture(autouse=True)
def fresh_cache():
    market_data.clear_cache()
    yield
    market_data.clear_cache()


def rule_payload(**overrides) -> dict:
    payload = {
        "name": "Trend + volatility",
        "symbols": ["xauusd", "EURUSD"],
        "timeframe": "M15",
        "provider": "demo",
        "conditions": [
            {"type": "ema_trend", "params": {}, "confluence": "HTF trend alignment"},
            {"type": "atr_filter", "params": {"min_atr_pct": 0.01}},
        ],
        "targets_rr": [1, 2],
        "cooldown_minutes": 30,
    }
    payload.update(overrides)
    return payload


def create_rule(client, **overrides) -> dict:
    response = client.post("/api/alerts/rules", json=rule_payload(**overrides))
    assert response.status_code == 201, response.text
    return response.json()


def add_alert(client, **overrides) -> int:
    values = dict(
        created_at=now_utc(),
        candle_time=now_utc(),
        symbol="XAUUSD",
        timeframe="M15",
        direction="long",
        entry_price=2654.0,
        stop_loss=2640.0,
        take_profits=[2682.0],
        details={"precheck": [{"code": "CHECKLIST_INCOMPLETE", "message": "Complete it", "severity": "block"}]},
        signal_text="XAUUSD BUY @ 2654.00\nSL 2640.00\nTP1 2682.00",
    )
    values.update(overrides)
    with client.app.state.session_factory() as db:
        alert = StrategyAlert(**values)
        db.add(alert)
        db.commit()
        return alert.id


def test_meta_describes_conditions_providers_and_push(client):
    meta = client.get("/api/alerts/meta").json()
    types = {item["key"]: item for item in meta["condition_types"]}
    assert types["liquidity_sweep"]["params"][0]["name"] == "lookback"
    assert types["liquidity_sweep"]["confluence"] == "Liquidity sweep"
    providers = {item["key"]: item for item in meta["providers"]}
    assert providers["demo"]["label"] == "Demo data (synthetic) - not real prices"
    assert providers["yahoo"]["symbol_map"]["XAUUSD"] == "GC=F"
    assert providers["binance"]["symbol_map"]["BTCUSD"] == "BTCUSDT"
    assert providers["twelvedata"]["symbol_map"]["EURUSD"] == "EUR/USD"
    assert providers["twelvedata"] | {"needs_key": True, "key_configured": False} == providers["twelvedata"]
    assert meta["timeframes"] == ["M1", "M5", "M15", "M30", "H1", "H4", "D1"]
    assert {m["key"] for m in meta["stop_methods"]} == {"swing", "signal", "atr"}
    assert meta["push"]["endpoint"] == "/api/alerts/candles" and meta["push"]["token_required"] is False


def test_conditions_count_under_their_untranslated_name_in_every_language(client):
    update_settings(client, language="it")
    types = {item["key"]: item for item in client.get("/api/alerts/meta").json()["condition_types"]}
    assert types["engulfing"]["label"] != "Engulfing candle"  # shown in Italian...
    assert types["engulfing"]["name"] == "Engulfing candle"  # ...but matched against the plan as stored


def test_provider_keys_are_stored_but_never_returned(client):
    response = client.put("/api/alerts/providers/twelvedata/key", json={"api_key": "td-secret-123456"})
    assert response.json() == {"provider": "twelvedata", "configured": True, "hint": "••••3456"}
    meta = client.get("/api/alerts/meta")
    assert "td-secret-123456" not in meta.text
    twelvedata = next(p for p in meta.json()["providers"] if p["key"] == "twelvedata")
    assert twelvedata["key_configured"] is True and twelvedata["key_hint"] == "••••3456"
    assert "td-secret-123456" not in client.get("/api/settings").text
    assert client.put("/api/alerts/providers/twelvedata/key", json={"api_key": ""}).json()["configured"] is False
    assert client.put("/api/alerts/providers/yahoo/key", json={"api_key": "x"}).status_code == 404


def test_rule_crud(client):
    strategy = client.get("/api/strategies").json()[0]
    rule = create_rule(client, strategy_id=strategy["id"], provider_symbols={"xauusd": "GC=F"})
    assert rule["symbols"] == ["XAUUSD", "EURUSD"]
    assert rule["provider_symbols"] == {"XAUUSD": "GC=F"}
    assert rule["strategy"]["name"] == strategy["name"]
    assert rule["conditions"][1]["params"] == {"period": 14, "min_atr_pct": 0.01}  # defaults filled in
    assert client.get(f"/api/alerts/rules/{rule['id']}").json()["name"] == "Trend + volatility"
    assert [r["id"] for r in client.get("/api/alerts/rules").json()] == [rule["id"]]

    updated = client.put(f"/api/alerts/rules/{rule['id']}", json=rule_payload(name="Renamed", enabled=False))
    assert updated.status_code == 200 and updated.json()["name"] == "Renamed" and updated.json()["enabled"] is False
    assert updated.json()["strategy"] is None
    assert client.delete(f"/api/alerts/rules/{rule['id']}").status_code == 204
    assert client.get(f"/api/alerts/rules/{rule['id']}").status_code == 404


@pytest.mark.parametrize(
    "overrides, message",
    [
        ({"conditions": [{"type": "astrology"}]}, "Unknown condition type"),
        ({"conditions": [{"type": "liquidity_sweep", "params": {"lookback": 1}}]}, "between 3 and 200"),
        ({"conditions": [{"type": "ema_trend", "params": {"fast": 200, "slow": 50}}]}, "fast EMA"),
        ({"conditions": []}, "at least 1"),
        ({"symbols": ["XAU USD"]}, "not a valid symbol"),
        ({"symbols": []}, "at least 1"),
        ({"symbols": [f"SYM{i}" for i in range(21)]}, "at most 20"),
        ({"timeframe": "M3"}, "M15"),
        ({"provider": "bloomberg"}, "demo"),
        ({"direction": "up"}, "both"),
        ({"stop_method": "hope"}, "swing"),
        ({"targets_rr": [2, 1]}, "ascending"),
        ({"targets_rr": [1, 2, 3, 4, 5]}, "at most 4"),
        ({"targets_rr": [-1]}, "between 0 and 50"),
        ({"cooldown_minutes": 2000}, "1440"),
        ({"risk_pct": 15}, "10"),
        ({"min_conditions": 3}, "cannot exceed"),
        ({"provider_symbols": {"BTCUSD": "BTC-USD"}}, "does not watch"),
    ],
)
def test_rule_validation(client, overrides, message):
    response = client.post("/api/alerts/rules", json=rule_payload(**overrides))
    assert response.status_code == 422
    assert message in response.text


def test_rule_with_unknown_strategy_is_rejected(client):
    response = client.post("/api/alerts/rules", json=rule_payload(strategy_id=9999))
    assert response.status_code == 422 and "Strategy 9999 not found" in response.text


def test_dry_run_returns_diagnostics_without_saving(client, monkeypatch):
    rule = create_rule(client)
    sent = []
    monkeypatch.setattr("trading_os.services.notify.send", lambda *args: sent.append(args))
    result = client.post(f"/api/alerts/rules/{rule['id']}/test").json()
    assert result["provider_label"] == "Demo data (synthetic) - not real prices"
    gold, euro = result["results"]
    assert gold["symbol"] == "XAUUSD" and gold["error"] == "" and gold["bars"] == 500 and gold["stale"] is False
    assert [c["type"] for c in gold["conditions"]] == ["ema_trend", "atr_filter"]
    assert all(isinstance(c["explanation"], str) and c["explanation"] for c in gold["conditions"])
    for item in (gold, euro):
        if item["found"]:
            assert item["direction"] in ("long", "short") and item["levels"]["entry"] > 0
            assert isinstance(item["precheck"], list)
        else:
            assert item["reason"]
    assert client.get("/api/alerts").json()["counts"]["all"] == 0 and sent == []

    def offline(*args, **kwargs):
        raise market_data.MarketDataError("Yahoo Finance did not answer in time - try again later.")

    monkeypatch.setattr(market_data, "fetch_candles", offline)
    failed = client.post(f"/api/alerts/rules/{rule['id']}/test").json()["results"][0]
    assert failed["error"].startswith("Yahoo Finance did not answer")
    assert client.post("/api/alerts/rules/999/test").status_code == 404


def test_scanner_settings(client):
    status = client.get("/api/alerts/scanner").json()
    assert status | {"enabled": False, "interval_sec": 60, "thread_running": False, "last_run_at": None} == status
    updated = client.put("/api/alerts/scanner", json={"enabled": True, "interval_sec": 900}).json()
    assert updated["enabled"] is True and updated["interval_sec"] == 900
    assert client.put("/api/alerts/scanner", json={"enabled": True, "interval_sec": 10}).status_code == 422
    assert client.put("/api/alerts/scanner", json={"enabled": True, "interval_sec": 7200}).status_code == 422


def test_manual_scan_endpoint_runs_with_the_scanner_off(client):
    create_rule(client)
    result = client.post("/api/alerts/scan").json()
    assert result["manual"] is True
    assert {row["symbol"] for row in result["results"]} == {"XAUUSD", "EURUSD"}
    assert all(row["status"] in ("alert", "no_setup", "cooldown") for row in result["results"])
    rule = client.get("/api/alerts/rules").json()[0]
    assert rule["last_checked_at"] is not None and rule["last_error"] == ""


def test_alert_feed_status_and_delete(client):
    first = add_alert(client)
    second = add_alert(client, symbol="EURUSD", created_at=now_utc() + timedelta(seconds=5))
    feed = client.get("/api/alerts").json()
    assert [a["id"] for a in feed["alerts"]] == [second, first]
    assert feed["counts"] == {"new": 2, "seen": 0, "dismissed": 0, "taken": 0, "all": 2}
    assert feed["alerts"][0]["precheck_passes"] is False

    assert client.post(f"/api/alerts/{first}/status", json={"status": "seen"}).json()["status"] == "seen"
    assert [a["id"] for a in client.get("/api/alerts?status=new").json()["alerts"]] == [second]
    assert client.post(f"/api/alerts/{first}/status", json={"status": "archived"}).status_code == 422
    assert client.post(f"/api/alerts/{first}/status", json={"status": "seen", "trade_id": 1}).status_code == 422
    assert client.post(f"/api/alerts/{first}/status", json={"status": "taken", "trade_id": 999}).status_code == 404

    yesterday = (now_utc() - timedelta(days=1)).isoformat()
    trade = client.post("/api/trades", json=trade_payload(status="closed", exit_price=2660.0, entry_time=yesterday))
    assert trade.status_code == 201, trade.text
    trade_id = trade.json()["trade"]["id"]
    taken = client.post(f"/api/alerts/{first}/status", json={"status": "taken", "trade_id": trade_id}).json()
    assert taken["status"] == "taken" and taken["trade_id"] == trade_id

    assert client.get(f"/api/alerts/{second}").json()["symbol"] == "EURUSD"
    assert client.delete(f"/api/alerts/{second}").status_code == 204
    assert client.get(f"/api/alerts/{second}").status_code == 404


def bar(time, o=2650.0, h=2652.0, low=2649.0, c=2651.0, **extra):
    return {"time": time, "open": o, "high": h, "low": low, "close": c, **extra}


def test_push_candles_upsert_and_validation(client):
    payload = {"symbol": "xauusd", "timeframe": "M15", "bars": [bar("2025-10-20T09:00:00"), bar("2025-10-20T09:15:00")]}
    first = client.post("/api/alerts/candles", json=payload).json()
    assert first | {"symbol": "XAUUSD", "received": 2, "inserted": 2, "updated": 0} == first
    payload["bars"] = [bar("2025-10-20T09:15:00", c=2650.5), bar("2025-10-20T09:30:00")]
    second = client.post("/api/alerts/candles", json=payload).json()
    assert (second["inserted"], second["updated"]) == (1, 1)
    with client.app.state.session_factory() as db:
        rows = db.scalars(select(Candle).order_by(Candle.time)).all()
        assert [r.close for r in rows] == [2651.0, 2650.5, 2651.0]

    bad = {"symbol": "XAUUSD", "timeframe": "M15", "bars": [bar("2025-10-20T09:00:00", h=2640.0)]}
    assert client.post("/api/alerts/candles", json=bad).status_code == 422
    too_many = {"symbol": "XAUUSD", "timeframe": "M15", "bars": [bar("2025-10-20T09:00:00")] * 2001}
    assert client.post("/api/alerts/candles", json=too_many).status_code == 422
    wrong_tf = {"symbol": "XAUUSD", "timeframe": "M2", "bars": [bar("2025-10-20T09:00:00")]}
    assert client.post("/api/alerts/candles", json=wrong_tf).status_code == 422
    negative = {"symbol": "XAUUSD", "timeframe": "M15", "bars": [bar("2025-10-20T09:00:00", low=-1)]}
    assert client.post("/api/alerts/candles", json=negative).status_code == 422


def test_push_candles_convert_aware_times_to_account_time(client):
    update_settings(client, timezone="Europe/Berlin")
    payload = {"symbol": "EURUSD", "timeframe": "H1", "bars": [bar("2025-10-20T07:00:00Z", 1.08, 1.09, 1.07, 1.085)]}
    assert client.post("/api/alerts/candles", json=payload).json()["first"] == "2025-10-20T09:00:00"


def test_push_token_is_required_once_configured(client):
    client.put("/api/alerts/providers/push/key", json={"api_key": "push-token-abc123"})
    payload = {"symbol": "XAUUSD", "timeframe": "M15", "bars": [bar("2025-10-20T09:00:00")]}
    assert client.post("/api/alerts/candles", json=payload).status_code == 401
    assert client.post("/api/alerts/candles", json={**payload, "token": "wrong"}).status_code == 401
    assert client.post("/api/alerts/candles", json={**payload, "token": "push-token-abc123"}).status_code == 200
    meta = client.get("/api/alerts/meta")
    assert meta.json()["push"]["token_required"] is True and "push-token-abc123" not in meta.text


def test_pushed_bars_that_are_still_forming_are_skipped(client):
    now = now_utc().replace(second=0)
    forming_open = now.replace(minute=now.minute - now.minute % 15)
    closed_open = forming_open - timedelta(minutes=15)
    payload = {
        "symbol": "XAUUSD",
        "timeframe": "M15",
        "bars": [bar(closed_open.isoformat()), bar(forming_open.isoformat())],
    }
    result = client.post("/api/alerts/candles", json=payload).json()
    assert (result["inserted"], result["skipped_forming"]) == (1, 1)
    only_forming = client.post("/api/alerts/candles", json={**payload, "bars": [bar(forming_open.isoformat())]}).json()
    assert only_forming["inserted"] == 0 and "still forming" in only_forming["note"]
    with client.app.state.session_factory() as db:
        assert [c.time for c in db.scalars(select(Candle))] == [closed_open]


def test_push_rules_find_broker_symbols_whatever_their_case(client):
    rule = create_rule(
        client,
        symbols=["XAUUSD"],
        provider="push",
        provider_symbols={"XAUUSD": "XAUUSDm"},
        conditions=[{"type": "atr_filter", "params": {"min_atr_pct": 0.0}}],
    )
    assert rule["provider_symbols"] == {"XAUUSD": "XAUUSDM"}
    start = now_utc().replace(second=0, minute=0) - timedelta(hours=10)
    bars = [bar((start + timedelta(minutes=15 * i)).isoformat()) for i in range(30)]
    assert (
        client.post("/api/alerts/candles", json={"symbol": "XAUUSDm", "timeframe": "M15", "bars": bars}).status_code
        == 200
    )
    (result,) = client.post(f"/api/alerts/rules/{rule['id']}/test").json()["results"]
    assert result["error"] == "" and result["bars"] == 30
    refused = client.post(
        "/api/alerts/rules",
        json=rule_payload(provider="push", symbols=["XAUUSD"], provider_symbols={"XAUUSD": "XAU/USD"}),
    )
    assert refused.status_code == 422 and "cannot be pushed" in refused.text


def test_conditions_needing_more_history_than_the_scanner_loads_are_refused(client):
    conditions = [{"type": "ema_trend", "params": {"fast": 50, "slow": 600}}]
    response = client.post("/api/alerts/rules", json=rule_payload(conditions=conditions))
    assert response.status_code == 422 and "Slow EMA must be between 3 and 479" in response.text


def test_rule_test_reports_an_unexpected_error_per_symbol(client, monkeypatch):
    from trading_os.services import alerts

    rule = create_rule(client)

    def broken(*args, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(alerts, "evaluate", broken)
    response = client.post(f"/api/alerts/rules/{rule['id']}/test")
    assert response.status_code == 200
    assert {r["error"] for r in response.json()["results"]} == {"Unexpected error: RuntimeError"}


def test_editing_a_rule_keeps_the_memory_of_alerted_candles(client):
    rule = create_rule(client)
    with client.app.state.session_factory() as db:
        from trading_os.models import AlertRule

        stored = db.get(AlertRule, rule["id"])
        stored.scan_state = {"XAUUSD": {"through": "2026-10-20T10:00:00", "candle": "2026-10-20T10:00:00"}}
        db.commit()
    client.put(f"/api/alerts/rules/{rule['id']}", json=rule_payload(name="Renamed"))
    with client.app.state.session_factory() as db:
        assert db.get(AlertRule, rule["id"]).scan_state == {"XAUUSD": {"candle": "2026-10-20T10:00:00"}}
