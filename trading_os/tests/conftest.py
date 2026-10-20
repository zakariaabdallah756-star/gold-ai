from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from trading_os.app import create_app
from trading_os.config import load_config


@pytest.fixture
def client(tmp_path):
    app = create_app(load_config(tmp_path / "data"))
    with TestClient(app) as test_client:
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
