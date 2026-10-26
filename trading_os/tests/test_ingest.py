"""External signal ingestion (v1.8): ``POST /api/v1/signals/incoming`` and its token management.

Covers the write-only Bearer token, the enable switch, strict payload validation (raw text or structured
fields), capture as ``source="api"``, the shared guarded auto-execution, and that the token never leaks or is
exported. Auto-execution reuses the signal arming (``auto_execute`` + a chosen account) and does not need the
in-app Telegram bot listener to be running.
"""

import json

from trading_os.models import AccountSettings

from .conftest import complete_checklist, update_settings
from .test_execution import paper_account

SIGNAL = "XAUUSD BUY\nEntry 2650\nSL 2645\nTP1 2660 TP2 2670"


def auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def set_token(client, token="s3cret-ingest-token-123") -> str:
    assert client.post("/api/ingest/token", json={"token": token}).status_code == 200
    return token


def arm_signals(client, account_id, confirm="", risk_pct=1.0):
    """Arm the shared signal auto-execution on an account (no Telegram bot token needed)."""
    client.put("/api/telegram/config", json={"auto_account_id": account_id, "risk_pct": risk_pct})
    return client.post("/api/telegram/arm", json={"auto_execute": True, "confirm": confirm})


# ------------------------------------------------------------------------ token


def test_status_starts_off_with_no_token(client):
    status = client.get("/api/ingest/status").json()
    assert status == {
        "enabled": False,
        "token_set": False,
        "token_hint": "",
        "endpoint": "/api/v1/signals/incoming",
    }


def test_a_short_token_is_refused(client):
    refused = client.post("/api/ingest/token", json={"token": "tooshort"})
    assert refused.status_code == 422
    assert client.get("/api/ingest/status").json()["token_set"] is False


def test_a_token_is_stored_write_only(client):
    token = set_token(client)
    status = client.get("/api/ingest/status").json()
    assert status["token_set"] is True and status["token_hint"] == token[-4:]
    # Only the sha256 hash and a short hint are kept — never the token, and the hash is not in the status.
    assert "token" not in status and "token_hash" not in status
    with client.app.state.session_factory() as db:
        row = db.get(AccountSettings, 1)
        assert row.ingest_token_hash and row.ingest_token_hash != token and len(row.ingest_token_hash) == 64
        assert token not in json.dumps({"hint": row.ingest_token_hint, "hash": row.ingest_token_hash})


def test_generating_a_token_returns_it_once_and_enables(client):
    body = client.post("/api/ingest/token/generate").json()
    assert body["token"] and body["enabled"] is True and body["token_set"] is True
    assert body["token_hint"] == body["token"][-4:]
    # The status endpoint never returns the token again.
    assert "token" not in client.get("/api/ingest/status").json()


def test_enabling_needs_a_token_first(client):
    assert client.put("/api/ingest/enabled", json={"enabled": True}).status_code == 409
    set_token(client)
    assert client.put("/api/ingest/enabled", json={"enabled": True}).json()["enabled"] is True


def test_clearing_the_token_disables_ingestion(client):
    client.post("/api/ingest/token/generate")
    cleared = client.post("/api/ingest/token", json={"token": ""}).json()
    assert cleared["token_set"] is False and cleared["enabled"] is False


# -------------------------------------------------------------------------- auth


def enabled_client(client, token="s3cret-ingest-token-123"):
    set_token(client, token)
    client.put("/api/ingest/enabled", json={"enabled": True})
    return token


def test_the_endpoint_rejects_a_missing_or_wrong_token(client):
    token = enabled_client(client)
    assert client.post("/api/v1/signals/incoming", json={"text": SIGNAL}).status_code == 401
    bad = client.post("/api/v1/signals/incoming", json={"text": SIGNAL}, headers=auth("wrong-token-abcdef"))
    assert bad.status_code == 401
    assert client.post("/api/v1/signals/incoming", json={"text": SIGNAL}, headers=auth(token)).status_code == 200


def test_a_valid_token_is_refused_while_ingestion_is_off(client):
    token = set_token(client)  # token set but not enabled
    off = client.post("/api/v1/signals/incoming", json={"text": SIGNAL}, headers=auth(token))
    assert off.status_code == 403


# ------------------------------------------------------------------- ingestion


def test_a_raw_text_signal_is_captured_as_source_api(client):
    token = enabled_client(client)
    body = client.post(
        "/api/v1/signals/incoming",
        json={"text": SIGNAL, "source_channel": "VIP Room"},
        headers=auth(token),
    ).json()
    sig = body["signal"]
    assert sig["source"] == "api" and sig["symbol"] == "XAUUSD" and sig["direction"] == "long"
    assert sig["stop_loss"] == 2645.0 and sig["take_profits"] == [2660.0, 2670.0] and sig["parse_ok"] is True
    assert sig["chat_title"] == "VIP Room" and body["auto"] is None
    # It shows up on the Signals list alongside Telegram captures.
    items = client.get("/api/telegram/signals").json()["items"]
    assert [i["source"] for i in items] == ["api"]


def test_a_structured_payload_is_accepted(client):
    token = enabled_client(client)
    body = client.post(
        "/api/v1/signals/incoming",
        json={"symbol": "xauusd", "action": "sell", "entry": 2650, "sl": 2660, "tp": [2630, 2620]},
        headers=auth(token),
    ).json()
    sig = body["signal"]
    assert sig["symbol"] == "XAUUSD" and sig["direction"] == "short" and sig["entry_price"] == 2650.0
    assert sig["stop_loss"] == 2660.0 and sig["take_profits"] == [2630.0, 2620.0] and sig["parse_ok"] is True


def test_a_single_tp_number_is_accepted(client):
    token = enabled_client(client)
    body = client.post(
        "/api/v1/signals/incoming",
        json={"symbol": "EURUSD", "side": "long", "entry": 1.08, "stop_loss": 1.07, "tp": 1.10},
        headers=auth(token),
    ).json()
    assert body["signal"]["take_profits"] == [1.10]


def test_an_empty_payload_is_refused(client):
    token = enabled_client(client)
    empty = client.post("/api/v1/signals/incoming", json={}, headers=auth(token))
    assert empty.status_code == 422


def test_a_structured_payload_without_a_side_is_refused(client):
    token = enabled_client(client)
    refused = client.post("/api/v1/signals/incoming", json={"symbol": "XAUUSD"}, headers=auth(token))
    assert refused.status_code == 422


def test_a_structured_payload_without_a_symbol_is_refused(client):
    token = enabled_client(client)
    refused = client.post("/api/v1/signals/incoming", json={"action": "buy"}, headers=auth(token))
    assert refused.status_code == 422


def test_unparseable_text_is_stored_but_not_parse_ok(client):
    token = enabled_client(client)
    body = client.post("/api/v1/signals/incoming", json={"text": "gm, no signal here"}, headers=auth(token)).json()
    assert body["signal"]["parse_ok"] is False and body["signal"]["symbol"] == "" and body["auto"] is None


def test_infinity_in_a_price_is_refused(client):
    token = enabled_client(client)
    bad = client.post(
        "/api/v1/signals/incoming",
        json={"symbol": "XAUUSD", "side": "buy", "entry": 2650, "sl": "Infinity"},
        headers=auth(token),
    )
    assert bad.status_code == 422


# -------------------------------------------------------- guarded auto-execution


def test_an_armed_account_auto_executes_an_incoming_signal(client):
    complete_checklist(client)
    update_settings(client, warn_outside_sessions=False, max_risk_pct=10)
    acct = paper_account(client, name="Paper", kind="demo")
    assert arm_signals(client, acct["id"]).status_code == 200
    token = enabled_client(client)
    body = client.post("/api/v1/signals/incoming", json={"text": SIGNAL}, headers=auth(token)).json()
    assert body["auto"]["status"] == "executed" and body["auto"]["execution_id"]
    assert body["signal"]["status"] == "sent"
    rows = client.get("/api/executions").json()["items"]
    assert rows and rows[0]["status"] == "filled"


def test_auto_execution_does_not_fire_when_not_armed(client):
    update_settings(client, warn_outside_sessions=False, max_risk_pct=10)
    paper_account(client, name="Paper", kind="demo")  # account exists but auto-execution not armed
    token = enabled_client(client)
    body = client.post("/api/v1/signals/incoming", json={"text": SIGNAL}, headers=auth(token)).json()
    assert body["auto"] is None
    assert client.get("/api/executions").json()["items"] == []


def test_auto_execution_skips_a_signal_without_an_entry(client):
    complete_checklist(client)
    update_settings(client, warn_outside_sessions=False, max_risk_pct=10)
    acct = paper_account(client, name="Paper", kind="demo")
    arm_signals(client, acct["id"])
    token = enabled_client(client)
    body = client.post(
        "/api/v1/signals/incoming",
        json={"symbol": "XAUUSD", "side": "sell", "sl": 2660, "tp": 2630},  # no entry price
        headers=auth(token),
    ).json()
    assert body["auto"]["status"] == "skipped" and "entry" in body["auto"]["message"].lower()
    assert client.get("/api/executions").json()["items"] == []


# ------------------------------------------------------------ the token is private


def test_the_ingest_token_is_never_exported(client):
    token = enabled_client(client)
    client.post("/api/v1/signals/incoming", json={"text": SIGNAL}, headers=auth(token))
    backup = client.get("/api/export/backup.json").json()
    dump = json.dumps(backup)
    assert token not in dump
    settings_row = backup["tables"]["account_settings"][0]
    assert "ingest_token_hash" not in settings_row and "ingest_token_hint" not in settings_row
    # The captured API signal is machine-local too, never in a backup.
    assert "telegram_signals" not in backup["tables"]
