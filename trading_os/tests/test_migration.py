"""Opening a v1.0 journal must add the new columns/tables without losing data."""

import sqlite3

from fastapi.testclient import TestClient

from trading_os.app import create_app
from trading_os.config import load_config

V1_SCHEMA = """
CREATE TABLE account_settings (
    id INTEGER NOT NULL, account_name VARCHAR(80) NOT NULL, currency VARCHAR(3) NOT NULL,
    starting_balance FLOAT NOT NULL, timezone VARCHAR(64) NOT NULL, default_risk_pct FLOAT NOT NULL,
    max_risk_pct FLOAT NOT NULL, min_rr FLOAT NOT NULL, trade_rule_mode VARCHAR(8) NOT NULL,
    max_daily_loss_pct FLOAT NOT NULL, max_trades_per_day INTEGER NOT NULL, daily_limit_mode VARCHAR(8) NOT NULL,
    require_checklist BOOLEAN NOT NULL, warn_outside_sessions BOOLEAN NOT NULL,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL, updated_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL,
    PRIMARY KEY (id)
);
CREATE TABLE trades (
    id INTEGER NOT NULL, status VARCHAR(8) NOT NULL, entry_time DATETIME NOT NULL, exit_time DATETIME,
    symbol VARCHAR(20) NOT NULL, direction VARCHAR(5) NOT NULL, entry_price FLOAT NOT NULL, stop_loss FLOAT NOT NULL,
    take_profit FLOAT, exit_price FLOAT, position_size FLOAT, risk_amount FLOAT, fees FLOAT NOT NULL, pnl FLOAT,
    pnl_manual BOOLEAN NOT NULL, planned_rr FLOAT, executed_rr FLOAT, r_multiple FLOAT, outcome VARCHAR(10),
    strategy VARCHAR(60) NOT NULL, timeframe VARCHAR(10) NOT NULL, session VARCHAR(40) NOT NULL,
    confluences JSON NOT NULL, emotion VARCHAR(20) NOT NULL, rule_compliant BOOLEAN, violations JSON NOT NULL,
    tags JSON NOT NULL, notes TEXT NOT NULL,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL, updated_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL,
    PRIMARY KEY (id)
);
INSERT INTO account_settings VALUES (1, 'Old', 'USD', 5000, 'UTC', 1, 2, 2, 'block', 3, 0, 'lock', 0, 1,
    '2026-10-01 00:00:00', '2026-10-01 00:00:00');
INSERT INTO trades VALUES (1, 'closed', '2026-10-01 10:00:00', '2026-10-01 12:00:00', 'XAUUSD', 'long',
    2650, 2645, 2665, 2660, 0.2, 100, 0, 200, 0, 3, 2, 2, 'win', 'SMC', 'M15', 'London', '["Key level (S/R)"]',
    'Calm', 1, '[]', '["v1"]', 'legacy trade', '2026-10-01 12:00:00', '2026-10-01 12:00:00');
"""


def test_v1_database_is_upgraded_in_place(tmp_path):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    with sqlite3.connect(data_dir / "journal.db") as connection:
        connection.executescript(V1_SCHEMA)

    app = create_app(load_config(data_dir))
    with TestClient(app) as client:
        settings = client.get("/api/settings").json()
        assert settings["account_name"] == "Old" and settings["starting_balance"] == 5000
        assert settings["news_mode"] == "warn" and settings["default_auto_be"] is True

        trade = client.get("/api/trades/1").json()
        assert trade["pnl"] == 200 and trade["tags"] == ["v1"] and trade["notes"] == "legacy trade"
        assert trade["mistakes"] == [] and trade["source"] == "manual" and trade["managed"] is False
        assert trade["targets"] == [] and trade["events"] == []

        # The upgraded file accepts new-style data.
        assert client.get("/api/mistakes").status_code == 200
        assert client.get("/api/analytics").json()["summary"]["net_pnl"] == 200

    with sqlite3.connect(data_dir / "journal.db") as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 2
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert {"trade_targets", "trade_events", "news_events", "daily_reviews", "mistake_types"} <= tables
