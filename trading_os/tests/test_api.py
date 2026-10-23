import io
import json
import struct
import zipfile
import zlib

from trading_os.services.backup import SCHEMA_VERSION

from .conftest import complete_checklist, trade_payload, update_settings, yesterday_iso


def tiny_png() -> bytes:
    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)

    raw = b"\x00\xff\x00\x00"  # one red pixel
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(raw))
        + chunk(b"IEND", b"")
    )


def test_bootstrap_seeds_a_default_plan(client):
    plan = client.get("/api/plan").json()
    assert plan["settings"]["min_rr"] == 2.0
    assert {m["symbol"] for m in plan["markets"]} >= {"XAUUSD", "EURUSD", "NAS100"}
    assert [s["name"] for s in plan["sessions"]] == ["London", "New York", "Asia"]
    assert {s["name"] for s in plan["strategies"]} == {"Price Action", "SMC", "Breakout"}
    assert plan["checklist_items"] and plan["confluences"]
    assert client.get("/").status_code == 200


def test_live_entry_is_gated_by_checklist(client):
    status = client.get("/api/risk/status").json()
    assert status["can_trade"] is False
    assert status["reasons"][0]["code"] == "CHECKLIST_INCOMPLETE"

    blocked = client.post("/api/trades", json=trade_payload())
    assert blocked.status_code == 423
    assert "CHECKLIST_INCOMPLETE" in {v["code"] for v in blocked.json()["violations"]}

    complete_checklist(client)
    assert client.get("/api/risk/status").json()["can_trade"] is True
    created = client.post("/api/trades", json=trade_payload())
    assert created.status_code == 201, created.text
    trade = created.json()["trade"]
    assert trade["risk_amount"] == 100.0  # 50 pips * $10 * 0.2 lots
    assert trade["planned_rr"] == 3.0


def test_min_rr_blocks_or_warns(client):
    complete_checklist(client)
    low_rr = trade_payload(take_profit=2655.0)
    response = client.post("/api/trades", json=low_rr)
    assert response.status_code == 423
    assert response.json()["violations"][0]["code"] == "RR_BELOW_MIN"

    update_settings(client, trade_rule_mode="warn", warn_outside_sessions=False)
    response = client.post("/api/trades", json=low_rr)
    assert response.status_code == 201
    body = response.json()
    assert [w["code"] for w in body["warnings"]] == ["RR_BELOW_MIN"]
    assert body["trade"]["rule_compliant"] is False


def test_historical_trades_are_recorded_with_violations(client):
    # No checklist and a 1:1 trade - but it happened yesterday, so it is journaled.
    payload = trade_payload(
        entry_time=yesterday_iso(), status="closed", take_profit=2655.0, exit_price=2655.0, fees=5.0
    )
    response = client.post("/api/trades", json=payload)
    assert response.status_code == 201, response.text
    trade = response.json()["trade"]
    assert {v["code"]: v["severity"] for v in trade["violations"]} == {"RR_BELOW_MIN": "warn"}
    assert trade["rule_compliant"] is False
    # Auto P&L: (2655 - 2650) * 100 oz * 0.2 lots = 100, minus 5 fees.
    assert trade["pnl"] == 95.0
    assert trade["pnl_manual"] is False
    assert trade["executed_rr"] == 1.0
    assert trade["r_multiple"] == 0.95
    assert trade["outcome"] == "win"
    assert trade["session"] == "New York"  # 12:00 UTC


def test_trade_validation(client):
    bad_stop = client.post("/api/trades", json=trade_payload(stop_loss=2660.0))
    assert bad_stop.status_code == 422
    assert "stop loss must be below" in bad_stop.text

    no_exit = client.post("/api/trades", json=trade_payload(status="closed", entry_time=yesterday_iso()))
    assert no_exit.status_code == 422

    unknown_market = trade_payload(symbol="FOO", status="closed", entry_time=yesterday_iso(), exit_price=2660.0)
    response = client.post("/api/trades", json=unknown_market)
    assert response.status_code == 422
    assert "net P&L" in response.json()["detail"]
    unknown_market["pnl"] = 42.0
    assert client.post("/api/trades", json=unknown_market).json()["trade"]["pnl"] == 42.0


def test_daily_loss_limit_locks_new_entries_but_allows_closing(client):
    complete_checklist(client)
    update_settings(client, warn_outside_sessions=False)
    open_trade = client.post("/api/trades", json=trade_payload()).json()["trade"]

    # Close today's trade at a loss larger than the 3% (300 USD) limit.
    closing = trade_payload(status="closed", exit_price=2630.0, pnl=-350.0, entry_time=open_trade["entry_time"])
    response = client.put(f"/api/trades/{open_trade['id']}", json=closing)
    assert response.status_code == 200, response.text
    assert response.json()["trade"]["r_multiple"] == -3.5

    status = client.get("/api/risk/status").json()
    assert status["state"] == "locked"
    assert status["can_trade"] is False
    assert status["loss_used"] == 350.0

    blocked = client.post("/api/trades", json=trade_payload())
    assert blocked.status_code == 423
    assert "DAILY_LOSS_LIMIT" in {v["code"] for v in blocked.json()["violations"]}

    # A closed trade logged after the fact is still recorded.
    after_the_fact = trade_payload(status="closed", exit_price=2640.0, pnl=-100.0)
    recorded = client.post("/api/trades", json=after_the_fact)
    assert recorded.status_code == 201
    assert "DAILY_LOSS_LIMIT" in {v["code"] for v in recorded.json()["warnings"]}

    update_settings(client, daily_limit_mode="warn")
    assert client.get("/api/risk/status").json()["state"] == "breached"
    assert client.post("/api/trades", json=trade_payload()).status_code == 201


def test_max_trades_per_day(client):
    complete_checklist(client)
    update_settings(client, max_trades_per_day=1, warn_outside_sessions=False)
    assert client.post("/api/trades", json=trade_payload()).status_code == 201
    response = client.post("/api/trades", json=trade_payload())
    assert response.status_code == 423
    assert "MAX_TRADES_REACHED" in {v["code"] for v in response.json()["violations"]}


def test_pre_trade_check_does_not_save(client):
    response = client.post("/api/trades/check", json=trade_payload(take_profit=2655.0))
    body = response.json()
    assert body["blocked"] is True
    assert {"RR_BELOW_MIN", "CHECKLIST_INCOMPLETE"} <= {v["code"] for v in body["violations"]}
    assert client.get("/api/trades").json()["total"] == 0


def test_position_size_endpoint(client):
    markets = {m["symbol"]: m for m in client.get("/api/markets").json()}
    response = client.post(
        "/api/risk/position-size",
        json={"market_id": markets["XAUUSD"]["id"], "entry_price": 2650, "stop_price": 2645, "take_profit": 2655},
    )
    body = response.json()
    assert body["lots"] == 0.2
    assert body["max_loss"] == 100.0
    assert {v["code"] for v in body["violations"]} >= {"RR_BELOW_MIN", "CHECKLIST_INCOMPLETE"}

    ger40 = client.post("/api/risk/position-size", json={"market_id": markets["GER40"]["id"], "stop_pips": 40})
    assert ger40.status_code == 422
    assert "conversion rate" in ger40.json()["detail"]


def test_screenshots_upload_link_and_cleanup(client, tmp_path):
    trade = client.post(
        "/api/trades", json=trade_payload(status="closed", entry_time=yesterday_iso(), exit_price=2660.0)
    ).json()["trade"]
    upload = client.post(
        f"/api/trades/{trade['id']}/screenshots",
        files={"file": ("chart.png", tiny_png(), "image/png")},
        data={"caption": "Entry"},
    )
    assert upload.status_code == 201, upload.text
    shot = upload.json()
    assert shot["src"].startswith("/uploads/") and shot["caption"] == "Entry"
    assert client.get(shot["src"]).content == tiny_png()

    rejected = client.post(
        f"/api/trades/{trade['id']}/screenshots", files={"file": ("x.png", b"<svg onload=alert(1)>", "image/png")}
    )
    assert rejected.status_code == 400

    link = client.post(f"/api/trades/{trade['id']}/screenshots/url", json={"url": "https://www.tradingview.com/x/abc/"})
    assert link.status_code == 201
    assert (
        client.post(f"/api/trades/{trade['id']}/screenshots/url", json={"url": "javascript:alert(1)"}).status_code
        == 422
    )

    assert len(client.get(f"/api/trades/{trade['id']}").json()["screenshots"]) == 2
    stored = tmp_path / "data" / "uploads" / shot["src"].rsplit("/", 1)[1]
    assert stored.exists()
    assert client.delete(f"/api/trades/{trade['id']}").status_code == 204
    assert not stored.exists()


def test_list_filters_search_and_sort(client):
    for pnl, symbol, notes in ((120.0, "XAUUSD", "clean breakout"), (-60.0, "EURUSD", "late entry")):
        payload = trade_payload(
            symbol=symbol, status="closed", entry_time=yesterday_iso(), exit_price=2660.0, pnl=pnl, notes=notes
        )
        if symbol == "EURUSD":
            payload.update(entry_price=1.08, stop_loss=1.075, take_profit=1.095, exit_price=1.077)
        assert client.post("/api/trades", json=payload).status_code == 201
    assert client.get("/api/trades?outcome=loss").json()["items"][0]["symbol"] == "EURUSD"
    assert client.get("/api/trades?q=breakout").json()["total"] == 1
    assert [t["pnl"] for t in client.get("/api/trades?sort=pnl&order=asc").json()["items"]] == [-60.0, 120.0]


def test_analytics_endpoint(client):
    for pnl in (200.0, -100.0, 50.0):
        client.post(
            "/api/trades", json=trade_payload(status="closed", entry_time=yesterday_iso(), exit_price=2660.0, pnl=pnl)
        )
    report = client.get("/api/analytics").json()
    summary = report["summary"]
    assert summary["trades"] == 3
    assert summary["net_pnl"] == 150.0
    assert summary["profit_factor"] == 2.5
    assert summary["expectancy"] == 50.0
    assert len(report["equity_curve"]) == 4
    assert report["equity_curve"][-1]["equity"] == 10_150.0
    assert report["breakdowns"]["symbol"][0]["key"] == "XAUUSD"
    assert client.get("/api/analytics?symbol=EURUSD").json()["summary"]["trades"] == 0


def test_checklist_save_and_history(client):
    items = client.get("/api/checklist-items").json()
    today = client.get("/api/checklist/today").json()["day"]
    partial = client.put(
        f"/api/checklist/{today}",
        json={"checked_item_ids": [items[0]["id"], 999], "bias": "bullish", "key_levels": "PDH 2412"},
    ).json()
    assert partial["checked_item_ids"] == [items[0]["id"]]  # unknown ids dropped
    assert partial["completed"] is False and partial["bias"] == "bullish"
    history = client.get("/api/checklist/history?days=7").json()
    assert len(history) == 7 and history[0]["day"] == today and history[0]["started"] is True


def test_plan_crud(client):
    created = client.post(
        "/api/markets",
        json={
            "symbol": "ustec",
            "name": "US Tech",
            "asset_class": "index",
            "quote_currency": "usd",
            "contract_size": 1,
            "pip_size": 1,
            "base_currency": "",
        },
    )
    assert created.status_code == 201, created.text
    market = created.json()
    assert market["symbol"] == "USTEC" and market["quote_currency"] == "USD" and market["base_currency"] is None
    assert client.post("/api/markets", json={**market, "id": None}).status_code == 409
    market["name"] = "Nasdaq"
    assert client.put(f"/api/markets/{market['id']}", json=market).json()["name"] == "Nasdaq"
    assert client.delete(f"/api/markets/{market['id']}").status_code == 204

    assert (
        client.post("/api/sessions", json={"name": "Bad", "start_utc": "10:00", "end_utc": "10:00"}).status_code == 422
    )
    assert (
        client.put("/api/settings", json={**client.get("/api/settings").json(), "timezone": "Mars/Base"}).status_code
        == 422
    )
    assert (
        client.put("/api/settings", json={**client.get("/api/settings").json(), "max_risk_pct": 0.5}).status_code == 422
    )


def test_export_and_restore_roundtrip(client):
    client.post(
        "/api/trades",
        json=trade_payload(status="closed", entry_time=yesterday_iso(), exit_price=2660.0, notes="=SUM(A1)"),
    )
    trade = client.get("/api/trades").json()["items"][0]
    client.post(f"/api/trades/{trade['id']}/screenshots", files={"file": ("c.png", tiny_png(), "image/png")})

    csv_text = client.get("/api/export/trades.csv").text
    assert csv_text.startswith("﻿id,status,entry_time")
    assert "'=SUM(A1)" in csv_text  # formula injection neutralised

    backup_json = client.get("/api/export/backup.json").json()
    assert backup_json["schema_version"] == SCHEMA_VERSION
    assert len(backup_json["tables"]["trades"]) == 1

    archive = zipfile.ZipFile(io.BytesIO(client.get("/api/export/backup.zip").content))
    assert any(name.startswith("uploads/") for name in archive.namelist())

    client.delete(f"/api/trades/{trade['id']}")
    assert client.get("/api/trades").json()["total"] == 0

    restored = client.post(
        "/api/import", files={"file": ("backup.json", json.dumps(backup_json).encode(), "application/json")}
    )
    assert restored.status_code == 200, restored.text
    assert restored.json()["restored"]["trades"] == 1
    assert restored.json()["safety_snapshot"].startswith("pre-restore-")
    again = client.get("/api/trades").json()["items"][0]
    assert again["id"] == trade["id"] and again["pnl"] == trade["pnl"]

    zipped = io.BytesIO()
    with zipfile.ZipFile(zipped, "w") as bad:
        bad.writestr("backup.json", json.dumps(backup_json))
        bad.writestr("../../evil.png", b"x")
    response = client.post("/api/import", files={"file": ("b.zip", zipped.getvalue(), "application/zip")})
    assert response.status_code == 200 and response.json()["restored"]["files"] == 0

    assert (
        client.post("/api/import", files={"file": ("x.json", b'{"app": "other"}', "application/json")}).status_code
        == 400
    )


def test_v1_backup_still_restores(client):
    """A backup made by v1.0 (no targets/events/news/reviews tables) restores."""
    client.post("/api/trades", json=trade_payload(status="closed", entry_time=yesterday_iso(), exit_price=2660.0))
    backup_json = client.get("/api/export/backup.json").json()
    backup_json["schema_version"] = 1
    for table in ("trade_targets", "trade_events", "news_events", "daily_reviews", "mistake_types"):
        backup_json["tables"].pop(table)
    for row in backup_json["tables"]["trades"]:
        for column in ("mistakes", "source", "managed", "current_stop", "remaining_size", "realized_pnl"):
            row.pop(column)
    restored = client.post(
        "/api/import", files={"file": ("v1.json", json.dumps(backup_json).encode(), "application/json")}
    )
    assert restored.status_code == 200, restored.text
    trade = client.get("/api/trades").json()["items"][0]
    assert trade["mistakes"] == [] and trade["source"] == "manual" and trade["realized_pnl"] == 0


def test_ai_key_is_never_exported_and_survives_restore(client):
    db = client.app.state.session_factory()
    try:
        from trading_os.models import AccountSettings

        db.get(AccountSettings, 1).ai_api_key = "sk-ant-secret"
        db.commit()
    finally:
        db.close()
    exported = client.get("/api/export/backup.json")
    assert "sk-ant-secret" not in exported.text and "ai_api_key" not in exported.text
    assert (
        client.post("/api/import", files={"file": ("b.json", exported.content, "application/json")}).status_code == 200
    )
    db = client.app.state.session_factory()
    try:
        assert db.get(AccountSettings, 1).ai_api_key == "sk-ant-secret"
    finally:
        db.close()
