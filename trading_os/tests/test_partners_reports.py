"""Partners and custom links, risk disclaimer acceptance, PDF performance/backtest reports."""

import dataclasses
import json
import re
import sqlite3

from fastapi.testclient import TestClient

from trading_os.app import create_app
from trading_os.config import load_config
from trading_os.services import disclaimer

from .conftest import trade_payload, yesterday_iso


def test_partners_seeded_once_and_custom_links(client, tmp_path):
    partners = client.get("/api/partners").json()
    names = [p["name"] for p in partners]
    assert {"FTMO", "FundedNext", "IC Markets", "Pepperstone"} <= set(names)
    assert all(p["featured"] for p in partners[:2]) and not partners[-1]["featured"]  # featured first
    ftmo = next(p for p in partners if p["name"] == "FTMO")
    assert ftmo["link"] == "https://ftmo.com"

    updated = client.put(f"/api/partners/{ftmo['id']}/link", json={"link_url": "https://ftmo.com/en/"})
    assert updated.status_code == 200 and updated.json()["link_url"] == "https://ftmo.com/en/"
    ftmo = next(p for p in client.get("/api/partners").json() if p["name"] == "FTMO")
    assert ftmo["link"] == "https://ftmo.com/en/"
    bad = client.put(f"/api/partners/{ftmo['id']}/link", json={"link_url": "javascript:alert(1)"})
    assert bad.status_code == 422

    # Restarting the app on the same data dir does not duplicate the defaults.
    again = create_app(dataclasses.replace(load_config(tmp_path / "data"), background_jobs=False))
    with TestClient(again, base_url="http://localhost") as second:
        assert len(second.get("/api/partners/all").json()) == len(client.get("/api/partners/all").json())


def test_partner_crud(client):
    created = client.post(
        "/api/partners",
        json={
            "name": "My Broker",
            "kind": "broker",
            "website_url": "https://example.com",
            "highlights": ["Low fees", ""],
        },
    )
    assert created.status_code == 201 and created.json()["highlights"] == ["Low fees"]
    pid = created.json()["id"]
    assert client.post("/api/partners", json={"name": "My Broker"}).status_code == 409
    assert client.post("/api/partners", json={"name": "X", "website_url": "ftp://example.com"}).status_code == 422
    body = {**created.json(), "active": False}
    assert client.put(f"/api/partners/{pid}", json=body).json()["active"] is False
    assert "My Broker" not in [p["name"] for p in client.get("/api/partners").json()]
    assert client.delete(f"/api/partners/{pid}").status_code == 204


def test_disclaimer_acceptance_and_version(client, monkeypatch):
    first = client.get("/api/disclaimer").json()
    assert first["accepted"] is False and first["version"] == disclaimer.DISCLAIMER_VERSION
    assert len(first["sections"]) >= 5 and "do not constitute financial advice" in first["footer"].lower()
    accepted = client.post("/api/disclaimer/accept").json()
    assert accepted["accepted"] is True and accepted["accepted_at"]
    assert client.get("/api/settings").json()["disclaimer_version"] == disclaimer.DISCLAIMER_VERSION

    monkeypatch.setattr(disclaimer, "DISCLAIMER_VERSION", "2099-01")
    assert client.get("/api/disclaimer").json()["accepted"] is False  # new text -> accept again


def assert_pdf(response):
    assert response.status_code == 200, response.text
    assert response.headers["content-type"] == "application/pdf"
    assert response.content.startswith(b"%PDF") and response.content.rstrip().endswith(b"%%EOF")
    assert 'attachment; filename="trading-os-' in response.headers["content-disposition"]
    return response.content


def test_performance_pdf(client):
    empty = assert_pdf(client.get("/api/reports/performance.pdf"))
    for pnl, symbol in ((250.0, "XAUUSD"), (-100.0, "XAUUSD"), (80.0, "EURUSD")):
        payload = trade_payload(symbol=symbol, status="closed", entry_time=yesterday_iso(), exit_price=2660.0, pnl=pnl)
        if symbol == "EURUSD":
            payload.update(entry_price=1.08, stop_loss=1.075, take_profit=1.095, exit_price=1.09)
        assert client.post("/api/trades", json=payload).status_code == 201
    full = assert_pdf(client.get("/api/reports/performance.pdf"))
    assert len(full) > len(empty) + 1500  # tables and the equity chart were rendered
    assert_pdf(client.get("/api/reports/performance.pdf?symbol=XAUUSD&date_from=2026-01-01"))


def test_backtest_pdf(client):
    sid = client.post("/api/backtest/strategies", json={"name": "PDF test"}).json()["id"]
    assert_pdf(client.get(f"/api/reports/backtest/{sid}.pdf"))  # no trades yet
    client.post(
        f"/api/backtest/strategies/{sid}/import",
        json={"csv": "date,direction,r\n2026-09-01,long,2\n2026-09-02,short,-1\n2026-09-03,long,1.5\n"},
    )
    assert_pdf(client.get(f"/api/reports/backtest/{sid}.pdf"))
    assert client.get("/api/reports/backtest/9999.pdf").status_code == 404


AFFILIATE_WORDS = re.compile(r"affiliate|referral|commission|sponsored", re.IGNORECASE)


def test_no_affiliate_wording_in_partners_or_disclaimer(client):
    payload = json.dumps([client.get("/api/partners").json(), client.get("/api/disclaimer").json()])
    assert not AFFILIATE_WORDS.search(payload)
    assert "is_referral" not in payload
    assert "organizational and educational purposes" in client.get("/api/disclaimer").json()["footer"]
    assert client.put("/api/partners/1/referral", json={"referral_url": "https://x.test"}).status_code in (404, 405)


def test_partner_referral_column_is_renamed_on_upgrade(tmp_path):
    """A v3 database stored the custom link as partners.referral_url; it is kept as link_url."""
    data = tmp_path / "data"
    first = create_app(dataclasses.replace(load_config(data), background_jobs=False))
    with TestClient(first, base_url="http://localhost"):
        pass
    first.state.engine.dispose()
    con = sqlite3.connect(data / "journal.db")
    con.execute("ALTER TABLE partners RENAME COLUMN link_url TO referral_url")
    con.execute("UPDATE partners SET referral_url = 'https://ftmo.com/en/' WHERE name = 'FTMO'")
    con.execute("PRAGMA user_version = 3")
    con.commit()
    con.close()

    upgraded = create_app(dataclasses.replace(load_config(data), background_jobs=False))
    with TestClient(upgraded, base_url="http://localhost") as client:
        ftmo = next(p for p in client.get("/api/partners").json() if p["name"] == "FTMO")
        assert ftmo["link"] == "https://ftmo.com/en/"
    con = sqlite3.connect(data / "journal.db")
    columns = {row[1] for row in con.execute("PRAGMA table_info(partners)")}
    assert "link_url" in columns and "referral_url" not in columns
    con.close()


def test_v3_backup_with_referral_links_restores(client):
    backup = client.get("/api/export/backup.json").json()
    backup["schema_version"] = 3
    for row in backup["tables"]["partners"]:
        row["referral_url"] = row.pop("link_url")
        if row["name"] == "FTMO":
            row["referral_url"] = "https://ftmo.com/en/"
    response = client.post("/api/import", files={"file": ("backup.json", json.dumps(backup), "application/json")})
    assert response.status_code == 200, response.text
    ftmo = next(p for p in client.get("/api/partners").json() if p["name"] == "FTMO")
    assert ftmo["link"] == "https://ftmo.com/en/"
