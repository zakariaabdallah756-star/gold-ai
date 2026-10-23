"""Plan integrity: guarded deletes, renames that follow through, stable history, seeding, settings, imports."""

import dataclasses
import io
import zipfile
from datetime import timedelta

from fastapi.testclient import TestClient

from trading_os.app import create_app
from trading_os.config import load_config
from trading_os.models import VisionAnalysis

from .conftest import complete_checklist, now_utc, trade_payload, yesterday_iso


def by_name(client, path: str, name: str) -> dict:
    return next(item for item in client.get(f"/api/{path}").json() if item["name"] == name)


def test_deleting_a_strategy_with_backtests_needs_confirmation(client):
    strategy = client.post("/api/backtest/strategies", json={"name": "Asia range"}).json()
    client.post(
        f"/api/backtest/strategies/{strategy['id']}/trades",
        json={"trade_time": yesterday_iso(), "direction": "long", "r_multiple": 1.5},
    )
    refused = client.delete(f"/api/strategies/{strategy['id']}")
    assert refused.status_code == 409 and "1 backtest trade" in refused.json()["detail"]
    assert client.delete(f"/api/strategies/{strategy['id']}?force=true").status_code == 204
    plain = client.post("/api/strategies", json={**by_name(client, "strategies", "Breakout"), "name": "Unused"}).json()
    assert client.delete(f"/api/strategies/{plain['id']}").status_code == 204


def test_renaming_a_confluence_or_session_follows_through_to_strategies(client):
    smc = by_name(client, "strategies", "SMC")
    body = {**smc, "required_confluences": ["Liquidity sweep"], "allowed_sessions": ["London"]}
    assert client.put(f"/api/strategies/{smc['id']}", json=body).status_code == 200
    rule = client.post(
        "/api/alerts/rules",
        json={
            "name": "Sweeps",
            "strategy_id": smc["id"],
            "symbols": ["XAUUSD"],
            "timeframe": "M15",
            "provider": "demo",
            "conditions": [{"type": "liquidity_sweep", "params": {}, "confluence": "Liquidity sweep"}],
        },
    )
    assert rule.status_code == 201, rule.text

    sweep = by_name(client, "confluences", "Liquidity sweep")
    client.put(f"/api/confluences/{sweep['id']}", json={**sweep, "name": "Liquidity grab"})
    london = by_name(client, "sessions", "London")
    client.put(f"/api/sessions/{london['id']}", json={**london, "name": "London open"})
    smc = by_name(client, "strategies", "SMC")
    assert smc["required_confluences"] == ["Liquidity grab"] and smc["allowed_sessions"] == ["London open"]
    assert (
        client.get(f"/api/alerts/rules/{rule.json()['id']}").json()["conditions"][0]["confluence"] == "Liquidity grab"
    )

    client.delete(f"/api/confluences/{sweep['id']}")
    assert by_name(client, "strategies", "SMC")["required_confluences"] == []


def test_template_changes_do_not_rewrite_past_checklist_days(client):
    yesterday = (now_utc().date() - timedelta(days=1)).isoformat()
    items = client.get("/api/checklist-items").json()
    done = client.put(f"/api/checklist/{yesterday}", json={"checked_item_ids": [i["id"] for i in items]}).json()
    assert done["completed"] is True
    client.post("/api/checklist-items", json={"category": "Macro", "label": "Check DXY", "required": True})
    history = {row["day"]: row for row in client.get("/api/checklist/history?days=3").json()}
    assert history[yesterday]["completed"] is True
    month = client.get("/api/analytics/calendar", params={"month": yesterday[:7]}).json()
    assert next(d for d in month["days"] if d["date"] == yesterday)["checklist_complete"] is True


def test_deleted_defaults_stay_deleted_after_a_restart(tmp_path):
    config = dataclasses.replace(load_config(tmp_path / "data"), background_jobs=False)
    with TestClient(create_app(config), base_url="http://localhost") as first:
        for item in first.get("/api/checklist-items").json():
            first.delete(f"/api/checklist-items/{item['id']}")
        for partner in first.get("/api/partners/all").json():
            first.delete(f"/api/partners/{partner['id']}")
    with TestClient(create_app(config), base_url="http://localhost") as second:
        assert second.get("/api/checklist-items").json() == []
        assert second.get("/api/partners/all").json() == []


def test_partial_settings_update_is_validated_against_the_stored_values(client):
    client.put("/api/settings", json={"default_risk_pct": 1.5, "max_risk_pct": 2.0})
    refused = client.put("/api/settings", json={"max_risk_pct": 1.2})
    assert refused.status_code == 422
    assert client.get("/api/plan").status_code == 200
    assert client.get("/api/settings").json()["max_risk_pct"] == 2.0


def test_zip_backup_includes_vision_chart_images(client):
    config = client.app.state.config
    name = "b" * 32 + ".png"
    (config.uploads_dir / name).write_bytes(b"\x89PNG\r\n\x1a\nfake")
    with client.app.state.session_factory() as db:
        db.add(VisionAnalysis(filename=name, status="done"))
        db.commit()
    archive = zipfile.ZipFile(io.BytesIO(client.get("/api/export/backup.zip").content))
    assert f"uploads/{name}" in archive.namelist()


def test_csv_import_reads_european_numbers_and_never_stores_unreadable_rows(client):
    strategy = client.post("/api/backtest/strategies", json={"name": "Imported"}).json()
    csv_text = (
        "date;side;entry;stop;tp;exit;notes\n"
        "2026-03-02 09:00;sell;151,235;151,5;150,8;150,9;\n"
        "2026-03-04 09:00;buy;2345,125;2340,5;2350,75;2350,75;" + "x" * 6000 + "\n"
        "2026-03-05 09:00;buy;2345;0;2350;0;exit not recorded\n"
        "2026-03-06 09:00;buy;inf;2340;2350;2348;\n"
    )
    response = client.post(f"/api/backtest/strategies/{strategy['id']}/import", json={"csv": csv_text})
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["imported"] == 2 and {e["line"] for e in result["errors"]} == {4, 5}
    trades = client.get(f"/api/backtest/strategies/{strategy['id']}/trades")
    assert trades.status_code == 200
    rows = sorted(trades.json(), key=lambda t: t["trade_time"])
    assert rows[0]["entry_price"] == 151.235 and rows[0]["outcome"] == "win"
    assert rows[1]["r_multiple"] > 0 and len(rows[1]["notes"]) == 5000


def test_trades_on_a_removed_market_stay_editable(client):
    complete_checklist(client)
    payload = trade_payload(status="closed", entry_time=yesterday_iso(), exit_time=yesterday_iso(), exit_price=2655.0)
    trade = client.post("/api/trades", json=payload).json()["trade"]
    market = next(m for m in client.get("/api/markets").json() if m["symbol"] == "XAUUSD")
    assert client.delete(f"/api/markets/{market['id']}").status_code == 204
    edited = client.put(f"/api/trades/{trade['id']}", json={**payload, "notes": "typo fixed"})
    assert edited.status_code == 200, edited.text
    assert edited.json()["trade"]["pnl"] == trade["pnl"] and edited.json()["trade"]["pnl_manual"] is False
