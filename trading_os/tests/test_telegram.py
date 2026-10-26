"""Telegram signal listener (v1.7), Bot API only.

No real network: ``telegram_listener._http_get`` is replaced with a scripted fake. These tests cover token
handling, update parsing, respecting a chat's content protection, the allow-list, the capture + offset loop and
the optional guarded auto-execution.
"""

import json

import pytest

from trading_os.models import TelegramListener
from trading_os.services import telegram_listener as tl

from .conftest import complete_checklist, update_settings
from .test_execution import paper_account, ready

TOKEN = "123456789:AAE-ExampleBotTokenForTestsOnly12345"


class FakeResponse:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code

    def json(self):
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload


class FakeTelegram:
    """Scripts getMe and getUpdates responses; records the calls."""

    def __init__(self):
        self.getme = {"ok": True, "result": {"username": "my_signal_bot", "first_name": "Signals"}}
        self.update_batches = []  # list of result lists, one per getUpdates call
        self.calls = []

    def __call__(self, url, params):
        self.calls.append((url, params))
        if url.endswith("/getMe"):
            return FakeResponse(self.getme)
        if url.endswith("/getUpdates"):
            batch = self.update_batches.pop(0) if self.update_batches else []
            if isinstance(batch, Exception):
                raise batch
            return FakeResponse({"ok": True, "result": batch})
        return FakeResponse({"ok": False, "description": "unknown method"}, 404)


@pytest.fixture
def tg(monkeypatch):
    fake = FakeTelegram()
    monkeypatch.setattr(tl, "_http_get", fake)
    return fake


def post(
    text,
    update_id=1,
    chat_id=-100123,
    title="Gold Signals",
    kind="channel",
    protected=False,
    caption=False,
    forward=False,
):
    message = {"message_id": update_id * 10, "chat": {"id": chat_id, "title": title, "type": kind}}
    message["caption" if caption else "text"] = text
    if protected:
        message["has_protected_content"] = True
    if forward:
        message["forward_from_chat"] = {"title": "Forwarded Room", "type": "channel"}
    key = "channel_post" if kind == "channel" else "message"
    return {"update_id": update_id, key: message}


SIGNAL = "XAUUSD BUY\nEntry 2650\nSL 2645\nTP1 2660 TP2 2670"


# ------------------------------------------------------------------------ token


def test_a_token_is_validated_and_stored_encrypted(client, tg):
    bad = client.put("/api/telegram/token", json={"token": "not-a-token"})
    assert bad.status_code == 422 and "bot token" in bad.json()["detail"]
    ok = client.put("/api/telegram/token", json={"token": TOKEN})
    assert ok.status_code == 200
    status = ok.json()
    assert status["token_set"] is True and status["bot_username"] == "my_signal_bot" and status["token_hint"]
    assert tg.calls[0][0].endswith("/getMe")
    # Stored encrypted, never in clear.
    with client.app.state.session_factory() as db:
        row = db.get(TelegramListener, 1)
        assert row.token_enc and row.token_enc != TOKEN and TOKEN not in json.dumps(row.allowed_chats)


def test_a_rejected_token_is_not_stored(client, tg):
    tg.getme = {"ok": False, "description": "Unauthorized"}
    refused = client.put("/api/telegram/token", json={"token": TOKEN})
    assert refused.status_code == 422 and "token" in refused.json()["detail"].lower()
    assert client.get("/api/telegram/status").json()["token_set"] is False


def test_the_listener_only_turns_on_with_a_token(client, tg):
    assert client.put("/api/telegram/config", json={"enabled": True}).status_code == 409
    client.put("/api/telegram/token", json={"token": TOKEN})
    assert client.put("/api/telegram/config", json={"enabled": True}).json()["enabled"] is True
    # Removing the token turns the listener off.
    client.put("/api/telegram/token", json={"token": ""})
    assert client.get("/api/telegram/status").json() == {
        **client.get("/api/telegram/status").json(),
        "enabled": False,
        "token_set": False,
    }


# ----------------------------------------------------------------- update parsing


def test_signals_are_parsed_from_channel_posts(client, tg):
    client.put("/api/telegram/token", json={"token": TOKEN})
    tg.update_batches = [[post(SIGNAL, update_id=5)]]
    result = client.post("/api/telegram/poll").json()
    assert result["captured"] == 1 and result["polled"] == 1
    [sig] = client.get("/api/telegram/signals").json()["items"]
    assert sig["symbol"] == "XAUUSD" and sig["direction"] == "long" and sig["entry_price"] == 2650.0
    assert sig["stop_loss"] == 2645.0 and sig["take_profits"] == [2660.0, 2670.0] and sig["parse_ok"] is True
    assert sig["chat_title"] == "Gold Signals" and sig["source"] == "channel"


def test_the_offset_advances_so_messages_are_never_read_twice(client, tg):
    client.put("/api/telegram/token", json={"token": TOKEN})
    tg.update_batches = [[post(SIGNAL, update_id=7)], []]
    client.post("/api/telegram/poll")
    second = client.post("/api/telegram/poll").json()
    assert second["captured"] == 0
    # getUpdates was asked for offset = last_update_id + 1.
    offsets = [params.get("offset") for url, params in tg.calls if url.endswith("/getUpdates")]
    assert offsets == [0, 8]
    assert len(client.get("/api/telegram/signals").json()["items"]) == 1


def test_protected_content_is_skipped_and_not_stored(client, tg):
    client.put("/api/telegram/token", json={"token": TOKEN})
    tg.update_batches = [[post(SIGNAL, update_id=3, protected=True), post(SIGNAL, update_id=4)]]
    result = client.post("/api/telegram/poll").json()
    assert result["skipped_protected"] == 1 and result["captured"] == 1
    items = client.get("/api/telegram/signals").json()["items"]
    assert len(items) == 1  # the protected message left no stored text
    assert all(SIGNAL not in json.dumps(i) or i["id"] for i in items)


def test_protected_content_is_captured_when_the_option_is_off(client, tg):
    client.put("/api/telegram/token", json={"token": TOKEN})
    client.put("/api/telegram/config", json={"skip_protected": False})
    tg.update_batches = [[post(SIGNAL, update_id=3, protected=True)]]
    assert client.post("/api/telegram/poll").json()["captured"] == 1


def test_an_allow_list_filters_chats(client, tg):
    client.put("/api/telegram/token", json={"token": TOKEN})
    client.put("/api/telegram/config", json={"allowed_chats": ["Gold Signals"]})
    tg.update_batches = [
        [post(SIGNAL, update_id=1, title="Gold Signals"), post(SIGNAL, update_id=2, title="Spam Room", chat_id=-999)]
    ]
    result = client.post("/api/telegram/poll").json()
    assert result["captured"] == 1 and result["skipped_other"] == 1
    assert client.get("/api/telegram/signals").json()["items"][0]["chat_title"] == "Gold Signals"


def test_captions_forwards_and_unparseable_messages(client, tg):
    client.put("/api/telegram/token", json={"token": TOKEN})
    tg.update_batches = [
        [
            post("just chatting, no signal here", update_id=1),
            post(SIGNAL, update_id=2, caption=True),
            post(SIGNAL, update_id=3, kind="private", forward=True),
            {"update_id": 4, "my_chat_member": {"foo": 1}},  # a non-message update
        ]
    ]
    result = client.post("/api/telegram/poll").json()
    assert result["captured"] == 3 and result["skipped_other"] == 1
    items = {i["id"]: i for i in client.get("/api/telegram/signals").json()["items"]}
    sources = sorted(i["source"] for i in items.values())
    assert sources == ["channel", "channel", "forward"]
    unparsed = [i for i in items.values() if not i["parse_ok"]]
    assert len(unparsed) == 1 and unparsed[0]["symbol"] == ""


def test_poll_errors_surface_and_set_last_error(client, tg, monkeypatch):
    client.put("/api/telegram/token", json={"token": TOKEN})

    def failing(url, params):
        if url.endswith("/getUpdates"):
            return FakeResponse({"ok": False, "description": "flood wait"}, 429)
        return FakeResponse(tg.getme)

    monkeypatch.setattr(tl, "_http_get", failing)
    failed = client.post("/api/telegram/poll")
    assert failed.status_code == 502 and "flood wait" in failed.json()["detail"]
    assert client.get("/api/telegram/status").json()["last_error"]


def test_signals_can_be_dismissed_and_cleared(client, tg):
    client.put("/api/telegram/token", json={"token": TOKEN})
    tg.update_batches = [[post(SIGNAL, update_id=1), post(SIGNAL, update_id=2)]]
    client.post("/api/telegram/poll")
    first = client.get("/api/telegram/signals").json()["items"][0]["id"]
    assert client.post(f"/api/telegram/signals/{first}/dismiss").json()["status"] == "dismissed"
    assert client.get("/api/telegram/status").json()["new_count"] == 1
    assert client.delete("/api/telegram/signals").status_code == 204
    assert client.get("/api/telegram/signals").json()["items"] == []


# ------------------------------------------------------------ pure parsing units


def test_extract_candidate_reads_chat_and_forward():
    cand = tl.extract_candidate(post(SIGNAL, update_id=9, kind="private", forward=True), True)
    assert cand.source == "forward" and cand.sender == "Forwarded Room" and cand.text.startswith("XAUUSD")
    assert tl.extract_candidate({"update_id": 1, "poll": {}}, True) is None
    protected = tl.extract_candidate(post(SIGNAL, protected=True), True)
    assert protected.protected is True and protected.text == ""


def test_chat_allowed_matches_id_or_title():
    cand = tl.Candidate(1, "-100999", "VIP Room", 1, "", "channel", "x")
    assert tl.chat_allowed(cand, []) is True
    assert tl.chat_allowed(cand, ["-100999"]) is True
    assert tl.chat_allowed(cand, ["@VIP Room"]) is True
    assert tl.chat_allowed(cand, ["Other"]) is False


# --------------------------------------------------------- guarded auto-execution


def arm_auto(client, tg, account_id, confirm=""):
    client.put("/api/telegram/token", json={"token": TOKEN})
    client.put("/api/telegram/config", json={"enabled": True, "auto_account_id": account_id, "risk_pct": 1.0})
    return client.post("/api/telegram/arm", json={"auto_execute": True, "confirm": confirm})


def test_auto_execution_is_off_until_armed_with_the_account(client, tg):
    ready(client)
    acct = paper_account(client, name="Paper", kind="demo")
    client.put("/api/telegram/token", json={"token": TOKEN})
    # No account chosen yet.
    assert client.post("/api/telegram/arm", json={"auto_execute": True}).status_code == 409
    armed = arm_auto(client, tg, acct["id"])
    assert armed.status_code == 200 and armed.json()["auto_execute"] is True


def test_a_real_account_needs_the_typed_name_to_arm_telegram(client, tg):
    ready(client)
    from .test_execution import bridge_account

    acct, _token = bridge_account(client)
    client.put("/api/telegram/token", json={"token": TOKEN})
    client.put("/api/telegram/config", json={"auto_account_id": acct["id"]})
    wrong = client.post("/api/telegram/arm", json={"auto_execute": True, "confirm": "nope"})
    assert wrong.status_code == 422
    assert (
        client.post("/api/telegram/arm", json={"auto_execute": True, "confirm": "MT5 Live"}).json()["auto_execute"]
        is True
    )


def test_an_armed_listener_auto_executes_a_clean_signal(client, tg):
    ready(client)
    acct = paper_account(client, name="Paper", kind="demo")
    update_settings(client, max_risk_pct=10)
    arm_auto(client, tg, acct["id"])
    tg.update_batches = [[post(SIGNAL, update_id=1)]]
    result = client.post("/api/telegram/poll").json()
    assert result["auto"] and result["auto"][0]["status"] == "executed"
    [sig] = client.get("/api/telegram/signals").json()["items"]
    assert sig["status"] == "sent" and sig["execution_id"]
    rows = client.get("/api/executions").json()["items"]
    assert rows and rows[0]["status"] == "filled"


def test_auto_execution_skips_a_signal_without_an_entry(client, tg):
    ready(client)
    acct = paper_account(client, name="Paper", kind="demo")
    arm_auto(client, tg, acct["id"])
    tg.update_batches = [[post("XAUUSD SELL now, SL 2660, TP 2630", update_id=1)]]  # market, no entry price
    result = client.post("/api/telegram/poll").json()
    assert result["auto"][0]["status"] == "skipped" and "entry" in result["auto"][0]["message"].lower()
    assert client.get("/api/executions").json()["items"] == []


def test_auto_execution_respects_the_checklist_gate(client, tg):
    acct = paper_account(client, name="Paper", kind="demo")
    update_settings(client, warn_outside_sessions=False, max_risk_pct=10)
    arm_auto(client, tg, acct["id"])  # checklist NOT done
    tg.update_batches = [[post(SIGNAL, update_id=1)]]
    assert client.post("/api/telegram/poll").json()["auto"][0]["status"] == "skipped"
    complete_checklist(client)
    tg.update_batches = [[post(SIGNAL, update_id=2)]]
    assert client.post("/api/telegram/poll").json()["auto"][0]["status"] == "executed"


def test_telegram_data_is_never_in_backups(client, tg):
    client.put("/api/telegram/token", json={"token": TOKEN})
    tg.update_batches = [[post(SIGNAL, update_id=1)]]
    client.post("/api/telegram/poll")
    backup = client.get("/api/export/backup.json").json()
    assert "telegram_listener" not in backup["tables"] and "telegram_signals" not in backup["tables"]
    assert TOKEN not in json.dumps(backup)
