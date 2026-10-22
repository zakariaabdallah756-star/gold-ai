import dataclasses
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from trading_os.app import create_app
from trading_os.config import load_config


@pytest.fixture
def client(tmp_path):
    # Background jobs stay off: tests run them on demand with app.state.scheduler.run_now(...).
    app = create_app(dataclasses.replace(load_config(tmp_path / "data"), background_jobs=False))
    with TestClient(app, base_url="http://localhost") as test_client:
        yield test_client


def now_utc() -> datetime:
    """Account time zone defaults to UTC, so this is the account's wall clock."""
    return datetime.now(timezone.utc).replace(tzinfo=None, microsecond=0)


def trade_payload(**overrides) -> dict:
    payload = {
        "status": "open",
        "entry_time": now_utc().isoformat(),
        "symbol": "XAUUSD",
        "direction": "long",
        "entry_price": 2650.0,
        "stop_loss": 2645.0,
        "take_profit": 2665.0,
        "position_size": 0.2,
        "strategy": "",
        "confluences": [],
        "emotion": "Calm",
    }
    payload.update(overrides)
    return payload


def yesterday_iso() -> str:
    return (now_utc() - timedelta(days=1)).replace(hour=12, minute=0).isoformat()


def complete_checklist(client) -> None:
    items = client.get("/api/checklist-items").json()
    today = client.get("/api/checklist/today").json()["day"]
    response = client.put(f"/api/checklist/{today}", json={"checked_item_ids": [i["id"] for i in items]})
    assert response.json()["completed"] is True


def update_settings(client, **changes) -> dict:
    settings = client.get("/api/settings").json()
    settings.update(changes)
    response = client.put("/api/settings", json=settings)
    assert response.status_code == 200, response.text
    return response.json()


@pytest.fixture(autouse=True)
def _no_network_lookups(monkeypatch):
    """The suite never looks up this computer's LAN addresses or name: tests that need them set their own."""
    from trading_os.services import network

    monkeypatch.setattr(network, "lan_addresses", lambda: ())
    monkeypatch.setattr(network, "machine_names", lambda: ("tradingpc", "tradingpc.local"))
