"""Localhost API hardening: host names, cross-origin writes, restore safety, exports, non-finite numbers."""

import io
import json
import time
import zipfile

from trading_os.services.signal_parser import parse_signal

from .conftest import trade_payload, update_settings, yesterday_iso


def test_only_local_names_and_ip_addresses_are_served(client):
    assert client.get("/api/plan", headers={"host": "attacker.example:8765"}).status_code == 400
    assert client.get("/api/plan", headers={"host": "localhost:8765"}).status_code == 200
    assert client.get("/api/plan", headers={"host": "192.168.1.20:8765"}).status_code == 200
    assert client.get("/api/plan", headers={"host": "[::1]:8765"}).status_code == 200


def test_extra_host_names_can_be_allowed(tmp_path, monkeypatch):
    import dataclasses

    from fastapi.testclient import TestClient

    from trading_os.app import create_app
    from trading_os.config import load_config

    monkeypatch.setenv("TRADING_OS_ALLOWED_HOSTS", "trading.lan, my-tunnel.example")
    app = create_app(dataclasses.replace(load_config(tmp_path / "data"), background_jobs=False))
    with TestClient(app, base_url="http://trading.lan") as named:
        assert named.get("/api/plan").status_code == 200
        assert named.get("/api/plan", headers={"host": "my-tunnel.example"}).status_code == 200
        assert named.get("/api/plan", headers={"host": "other.example"}).status_code == 400
        # Behind a proxy that rewrites Host, writes from the trusted name still work.
        proxied = named.post(
            "/api/disclaimer/accept",
            headers={"host": "localhost:8765", "origin": "https://my-tunnel.example", "sec-fetch-site": "cross-site"},
        )
        assert proxied.status_code == 200


def test_other_websites_cannot_change_data(client):
    backup = json.dumps({"app": "trading_os", "schema_version": 4, "tables": {}})
    files = {"file": ("backup.json", backup, "application/json")}
    assert client.post("/api/import", files=files, headers={"origin": "http://evil.example"}).status_code == 403
    assert client.post("/api/import", files=files, headers={"sec-fetch-site": "cross-site"}).status_code == 403
    assert client.post("/api/import", files=files, headers={"origin": "null"}).status_code == 403
    assert (
        client.post("/api/killswitch", json={"hours": 4}, headers={"origin": "http://localhost:3000"}).status_code
        == 403
    )
    # The app itself (same origin) and scripts without an Origin header keep working.
    assert client.post("/api/killswitch", json={"hours": 4}, headers={"origin": "http://localhost"}).status_code == 200
    assert client.post("/api/disclaimer/accept").status_code == 200
    assert client.get("/api/plan", headers={"origin": "http://evil.example"}).status_code == 200


def test_back_to_back_restores_keep_every_safety_snapshot(client):
    backup = client.get("/api/export/backup.json").content
    names = set()
    for _ in range(3):
        response = client.post("/api/import", files={"file": ("backup.json", backup, "application/json")})
        assert response.status_code == 200, response.text
        names.add(response.json()["safety_snapshot"])
    assert len(names) == 3


def test_zip_restore_refuses_oversized_members_before_touching_data(client):
    client.post("/api/trades", json=trade_payload(status="closed", entry_time=yesterday_iso(), exit_price=2655.0))
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("backup.json", client.get("/api/export/backup.json").content)
        archive.writestr("uploads/" + "a" * 32 + ".png", b"\0" * (30 * 1024 * 1024))  # compresses to almost nothing
    response = client.post("/api/import", files={"file": ("backup.zip", buffer.getvalue(), "application/zip")})
    assert response.status_code == 400 and "larger than the upload limit" in response.json()["detail"]
    assert len(client.get("/api/trades").json()["items"]) == 1


def test_restore_refuses_settings_the_app_cannot_load(client):
    client.post("/api/trades", json=trade_payload(status="closed", entry_time=yesterday_iso(), exit_price=2655.0))
    backup = client.get("/api/export/backup.json").json()
    backup["tables"]["account_settings"][0]["timezone"] = "Mars/Olympus"
    response = client.post("/api/import", files={"file": ("b.json", json.dumps(backup), "application/json")})
    assert response.status_code == 400 and "timezone" in response.json()["detail"]
    assert client.get("/api/plan").status_code == 200  # nothing was replaced
    assert len(client.get("/api/trades").json()["items"]) == 1


def test_pdf_reports_treat_names_and_filters_as_text(client):
    update_settings(client, account_name="Desk <i>")
    client.post("/api/trades", json=trade_payload(status="closed", entry_time=yesterday_iso(), exit_price=2655.0))
    response = client.get("/api/reports/performance.pdf", params={"strategy": '<img src="/etc/hostname" width="9"/>'})
    assert response.status_code == 200 and response.content.startswith(b"%PDF")
    strategy = client.post("/api/backtest/strategies", json={"name": "RSI <b50 reclaim"}).json()
    assert client.get(f"/api/reports/backtest/{strategy['id']}.pdf").status_code == 200


def test_backtest_csv_export_neutralises_formulas_in_every_text_cell(client):
    strategy = client.post("/api/backtest/strategies", json={"name": "Forum import"}).json()
    csv_text = 'date;side;r;symbol;session;timeframe;notes\n2026-01-02;buy;1;=1+2;=HYPERLINK("http://e.vil","x");@SUM(1);-2+3\n'
    imported = client.post(
        f"/api/backtest/strategies/{strategy['id']}/import", json={"csv": csv_text, "dry_run": False}
    )
    assert imported.status_code == 200, imported.text
    exported = client.get(f"/api/backtest/strategies/{strategy['id']}/export.csv").text.splitlines()[1]
    for cell in ("'=1+2", "'=HYPERLINK", "'@SUM(1)", "'-2+3"):
        assert cell in exported


def test_infinity_and_nan_are_rejected(client):
    body = json.dumps(trade_payload(status="closed", entry_time=yesterday_iso(), exit_price=2655.0)).replace(
        '"exit_price": 2655.0', '"exit_price": 2655.0, "pnl": Infinity'
    )
    response = client.post("/api/trades", content=body, headers={"content-type": "application/json"})
    assert response.status_code == 422
    trade = client.post("/api/trades", json=trade_payload()).json()
    if "trade" in trade:
        bad = client.post(
            f"/api/trades/{trade['trade']['id']}/price",
            content='{"price": NaN}',
            headers={"content-type": "application/json"},
        )
        assert bad.status_code == 422


def test_signal_parser_stays_fast_on_long_whitespace():
    started = time.perf_counter()
    parse_signal("buy gold sl 1 at" + " " * 20_000, ["XAUUSD"])
    parse_signal("sell eurusd tp at" + "\n" * 20_000, ["EURUSD"])
    assert time.perf_counter() - started < 1.0
