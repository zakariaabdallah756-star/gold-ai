"""Notification channels, delivery, settings, history and background watchers (no network)."""

import hashlib
import hmac
import json
from datetime import timedelta

import httpx
import pytest

from trading_os.models import NotificationChannel, NotificationLog
from trading_os.services import account, notify, notify_channels

from .conftest import now_utc

NTFY_TOKEN = "tk_secretntfytoken1234567890abc"
TELEGRAM_TOKEN = "123456789:AAHsecretTelegramTokenValue_abcdefgh"
DISCORD_URL = "https://discord.com/api/webhooks/1234567890/secretDiscordTokenXYZ_987"
SLACK_URL = "https://hooks.slack.com/services/T000/B000/secretSlackTokenQRS"
PUSHOVER_APP = "azGDORePK8gMaC0QOYAMyEEuzJnyUi"
PUSHOVER_USER = "uQiRzpo4DXghDmr9QzzfQu27cmVRsG"
WEBHOOK_URL = "https://hooks.example.net/trading/secretWebhookPath42"
WEBHOOK_SECRET = "s3cr3t-signing-key-0042"
SECRETS = (NTFY_TOKEN, TELEGRAM_TOKEN, DISCORD_URL, SLACK_URL, PUSHOVER_APP, PUSHOVER_USER, WEBHOOK_URL, WEBHOOK_SECRET)

CONFIGS = {
    "ntfy": {"server": "https://ntfy.sh/", "topic": "tradingos_k3x9q2", "token": NTFY_TOKEN},
    "telegram": {"bot_token": TELEGRAM_TOKEN, "chat_id": "-1001234567"},
    "discord": {"webhook_url": DISCORD_URL},
    "slack": {"webhook_url": SLACK_URL},
    "pushover": {"app_token": PUSHOVER_APP, "user_key": PUSHOVER_USER},
    "webhook": {"url": WEBHOOK_URL, "secret": WEBHOOK_SECRET},
}


class FakeHttp:
    """Records every outbound request instead of sending it."""

    def __init__(self):
        self.calls: list[dict] = []
        self.responses: dict[str, object] = {}  # url prefix -> httpx.Response | Exception

    def __call__(self, url, body, headers):
        self.calls.append({"url": url, "body": body, "headers": headers})
        for prefix, response in self.responses.items():
            if url.startswith(prefix):
                if isinstance(response, Exception):
                    raise response
                return response
        return httpx.Response(200, json={"ok": True})

    def json(self, index=-1) -> dict:
        return json.loads(self.calls[index]["body"])


@pytest.fixture
def http(monkeypatch):
    fake = FakeHttp()
    monkeypatch.setattr(notify_channels, "_http_post", fake)
    return fake


def enable(client, **changes):
    body = {"enabled": True, "events": ["strategy_alert", "risk", "news"], **changes}
    response = client.put("/api/notifications/settings", json=body)
    assert response.status_code == 200, response.text
    return response.json()


def add_channel(client, kind, **extra):
    response = client.post("/api/notifications/channels", json={"kind": kind, "config": CONFIGS[kind], **extra})
    assert response.status_code == 201, response.text
    return response.json()


def send_note(client, note):
    with client.app.state.session_factory() as db:
        settings = account.get_settings(db)
        row = notify.send(db, settings, note)
        return None if row is None else {"status": row.status, "results": row.results}


def alert_note(**overrides):
    values = {
        "event": "strategy_alert",
        "title": "XAUUSD long setup",
        "body": "London breakout: entry 2650.0, SL 2645.0, TP 2665.0",
        "url": "/#/alerts?id=3",
        "priority": "high",
        "data": {"alert_id": 3},
        "tags": ["XAUUSD", "long"],
    }
    values.update(overrides)
    return notify.Note(**values)


def log_rows(client):
    return client.get("/api/notifications/log").json()


# ----------------------------------------------------------- request shapes


def test_ntfy_request_shape_and_priority_mapping(client, http):
    enable(client, app_url="http://192.168.1.20:8765/")
    add_channel(client, "ntfy")
    for priority, expected in (("low", 2), ("default", 3), ("high", 5)):
        send_note(client, alert_note(priority=priority))
        call = http.calls[-1]
        assert call["url"] == "https://ntfy.sh"
        assert call["headers"]["Authorization"] == f"Bearer {NTFY_TOKEN}"
        assert call["headers"]["Content-Type"] == "application/json"
        payload = http.json()
        assert payload["priority"] == expected
        assert payload["topic"] == "tradingos_k3x9q2"
        assert payload["title"] == "XAUUSD long setup"
        assert payload["message"].startswith("London breakout")
        assert payload["tags"] == ["XAUUSD", "long"]
        assert payload["click"] == "http://192.168.1.20:8765/#/alerts?id=3"


def test_remote_channels_omit_link_without_app_url(client, http):
    enable(client)
    add_channel(client, "ntfy")
    add_channel(client, "telegram")
    send_note(client, alert_note())
    ntfy, telegram = (json.loads(call["body"]) for call in http.calls)
    assert "click" not in ntfy
    assert telegram["text"] == "XAUUSD long setup\n\nLondon breakout: entry 2650.0, SL 2645.0, TP 2665.0"


def test_telegram_request_shape(client, http):
    enable(client, app_url="https://journal.example.com")
    add_channel(client, "telegram")
    send_note(client, alert_note())
    call = http.calls[0]
    assert call["url"] == f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = http.json()
    assert payload == {
        "chat_id": "-1001234567",
        "text": "XAUUSD long setup\n\nLondon breakout: entry 2650.0, SL 2645.0, TP 2665.0\n\n"
        "https://journal.example.com/#/alerts?id=3",
        "disable_web_page_preview": True,
    }


def test_telegram_text_is_truncated_but_keeps_the_link(client, http):
    enable(client, app_url="https://journal.example.com")
    add_channel(client, "telegram")
    send_note(client, alert_note(body="x" * 6000))
    text = http.json()["text"]
    assert len(text) <= 4096
    assert text.endswith("https://journal.example.com/#/alerts?id=3")


def test_discord_slack_and_pushover_shapes(client, http):
    enable(client, app_url="https://journal.example.com")
    add_channel(client, "discord")
    add_channel(client, "slack")
    add_channel(client, "pushover")
    send_note(client, alert_note(title="Gold <long> & more", priority="low"))
    by_url = {call["url"]: json.loads(call["body"]) for call in http.calls}

    discord = by_url[DISCORD_URL]
    assert discord["allowed_mentions"] == {"parse": []}
    assert discord["content"].startswith("Gold <long> & more\nLondon breakout")

    slack = by_url[SLACK_URL]
    assert slack["text"].startswith("Gold &lt;long&gt; &amp; more\n")

    pushover = by_url["https://api.pushover.net/1/messages.json"]
    assert pushover["token"] == PUSHOVER_APP and pushover["user"] == PUSHOVER_USER
    assert pushover["priority"] == -1
    assert pushover["url"] == "https://journal.example.com/#/alerts?id=3"
    assert pushover["url_title"] == "Open Trading OS"


def test_user_agent_header_is_set(monkeypatch):
    seen = {}

    def fake_post(url, **kwargs):
        seen.update(kwargs, url=url)
        return httpx.Response(200)

    monkeypatch.setattr(httpx, "post", fake_post)
    notify_channels._http_post("https://ntfy.sh", b"{}", {"Content-Type": "application/json"})
    assert seen["headers"]["User-Agent"].startswith("TradingOS/")
    assert seen["follow_redirects"] is False
    assert seen["timeout"] <= 10


def test_webhook_payload_and_hmac_signature(client, http):
    enable(client, app_url="https://journal.example.com")
    add_channel(client, "webhook")
    send_note(client, alert_note())
    call = http.calls[0]
    assert call["url"] == WEBHOOK_URL
    expected = "sha256=" + hmac.new(WEBHOOK_SECRET.encode(), call["body"], hashlib.sha256).hexdigest()
    assert call["headers"]["X-TradingOS-Signature"] == expected
    payload = http.json()
    assert payload["event"] == "strategy_alert"
    assert payload["title"] == "XAUUSD long setup"
    assert payload["url"] == "https://journal.example.com/#/alerts?id=3"
    assert payload["data"] == {"alert_id": 3}
    assert payload["sent_at"][:4].isdigit() and payload["sent_at"].endswith("+00:00")


def test_webhook_without_secret_is_unsigned(client, http):
    enable(client)
    client.post(
        "/api/notifications/channels",
        json={"kind": "webhook", "config": {"url": "http://homeassistant.local:8123/api/webhook/x"}},
    )
    send_note(client, alert_note())
    assert "X-TradingOS-Signature" not in http.calls[0]["headers"]


# --------------------------------------------------------------- validation


@pytest.mark.parametrize(
    ("kind", "config", "message"),
    [
        ("telegram", {"bot_token": "not-a-token", "chat_id": "123"}, "bot token"),
        ("telegram", {"bot_token": TELEGRAM_TOKEN, "chat_id": "me"}, "chat id"),
        ("discord", {"webhook_url": "https://evil.example.com/api/webhooks/1/x"}, "Discord webhook"),
        ("discord", {"webhook_url": "http://discord.com/api/webhooks/1/x"}, "https://"),
        ("slack", {"webhook_url": "https://hooks.slack.com.evil.io/services/x"}, "Slack"),
        ("ntfy", {"topic": "bad topic!"}, "letters, digits"),
        ("ntfy", {"topic": ""}, "topic"),
        ("ntfy", {"server": "ftp://ntfy.sh", "topic": "ok_topic"}, "ntfy server"),
        ("pushover", {"app_token": "short", "user_key": PUSHOVER_USER}, "API token"),
        ("webhook", {"url": "ftp://example.com/hook"}, "webhook URL"),
        ("webhook", {"url": "https://user:pw@example.com/hook"}, "user name"),
        ("pigeon", {}, "Unknown channel type"),
        ("webpush", {"endpoint": "https://fcm.googleapis.com/x"}, "Enable on this device"),
    ],
)
def test_invalid_channel_configs_are_rejected(client, kind, config, message):
    response = client.post("/api/notifications/channels", json={"kind": kind, "config": config})
    assert response.status_code == 422
    assert message in response.json()["detail"]


def test_settings_validation_and_normalisation(client):
    settings = enable(client, app_url="http://192.168.1.20:8765/", news_reminder_min=30, events=["risk", "risk"])
    assert settings["app_url"] == "http://192.168.1.20:8765"
    assert settings["events"] == ["risk"]
    assert settings["news_reminder_min"] == 30
    assert {e["id"] for e in settings["events_available"]} == {"strategy_alert", "risk", "news"}
    assert settings["webpush_available"] is True
    for bad in ({"app_url": "192.168.1.20"}, {"app_url": "javascript:alert(1)"}, {"news_reminder_min": 121}):
        assert client.put("/api/notifications/settings", json=bad).status_code == 422
    assert client.put("/api/notifications/settings", json={"events": ["test"]}).status_code == 422
    assert client.put("/api/notifications/settings", json={"vapid_contact": "nobody"}).status_code == 422
    assert client.put("/api/notifications/settings", json={"vapid_contact": "mailto:me@example.com"}).status_code == 200


# ------------------------------------------------------------ masking/secrets


def test_configs_are_masked_and_unchanged_masks_keep_secrets(client, http):
    enable(client)
    channel = add_channel(client, "ntfy", name="Phone")
    assert channel["config"] == {"server": "https://ntfy.sh", "topic": "tr••••", "token": "••••0abc"}

    listed = client.get("/api/notifications/channels").json()[0]
    edited = client.put(
        f"/api/notifications/channels/{listed['id']}",
        json={"name": "My phone", "config": {**listed["config"], "server": "https://ntfy.example.org"}},
    )
    assert edited.status_code == 200, edited.text
    assert edited.json()["name"] == "My phone"
    send_note(client, alert_note())
    assert http.calls[-1]["url"] == "https://ntfy.example.org"
    assert http.calls[-1]["headers"]["Authorization"] == f"Bearer {NTFY_TOKEN}"
    assert http.json()["topic"] == "tradingos_k3x9q2"

    client.put(
        f"/api/notifications/channels/{listed['id']}",
        json={"config": {"server": "https://ntfy.sh", "topic": "new_topic", "token": ""}},
    )
    send_note(client, alert_note())
    assert "Authorization" not in http.calls[-1]["headers"]
    assert http.json()["topic"] == "new_topic"


def test_secrets_never_leave_the_server(client, http):
    enable(client)
    created = [add_channel(client, kind) for kind in CONFIGS]
    # A service that echoes the secret back in its error must not leak it into the history.
    http.responses["https://api.telegram.org"] = httpx.Response(
        401, json={"description": f"Unauthorized {TELEGRAM_TOKEN}"}
    )
    http.responses[WEBHOOK_URL] = httpx.Response(500, json={"error": f"boom {WEBHOOK_SECRET}"})
    client.get("/api/notifications/webpush/key")
    responses = [
        json.dumps(created),
        client.get("/api/notifications/channels").text,
        client.post("/api/notifications/test").text,
        client.get("/api/notifications/log").text,
        client.get("/api/notifications/settings").text,
        client.get("/api/export/backup.json").text,
    ]
    for item in created:
        responses.append(client.put(f"/api/notifications/channels/{item['id']}", json={"config": item["config"]}).text)
        responses.append(client.post(f"/api/notifications/channels/{item['id']}/test").text)
    with client.app.state.session_factory() as db:
        private_key = account.get_settings(db).vapid_private_key
    assert private_key
    blob = "\n".join(responses)
    for secret in (*SECRETS, private_key, "tradingos_k3x9q2"):
        assert secret not in blob
    results = [r for row in client.get("/api/notifications/log").json() for r in row["results"]]
    telegram = next(r for r in results if r["kind"] == "telegram")
    assert telegram["error"].startswith("HTTP 401: Unauthorized")


# -------------------------------------------------------------- event rules


def test_master_switch_and_event_filters(client, http):
    add_channel(client, "ntfy")
    assert send_note(client, alert_note()) is None  # notifications are off by default
    assert http.calls == [] and log_rows(client) == []

    enable(client, events=["risk"])
    assert send_note(client, alert_note()) is None  # strategy alerts disabled
    assert http.calls == [] and log_rows(client) == []

    test = client.post("/api/notifications/test").json()  # test always sends
    assert test["status"] == "sent" and len(http.calls) == 1

    client.put("/api/notifications/settings", json={"enabled": False})
    assert client.post("/api/notifications/test").json()["status"] == "sent"


def test_channel_event_subsets_and_disabled_channels(client, http):
    enable(client)
    risk_only = add_channel(client, "ntfy", events=["risk"])
    everything = add_channel(client, "telegram")
    add_channel(client, "discord", enabled=False)
    result = send_note(client, alert_note())
    assert [r["channel_id"] for r in result["results"]] == [everything["id"]]
    result = send_note(client, alert_note(event="risk", title="Daily loss limit reached"))
    assert {r["channel_id"] for r in result["results"]} == {risk_only["id"], everything["id"]}


def test_no_channel_is_logged_as_skipped(client, http):
    enable(client)
    result = send_note(client, alert_note())
    assert result == {"status": "skipped", "results": []}
    assert log_rows(client)[0]["status"] == "skipped"


def test_partial_and_failed_deliveries_record_errors(client, http):
    enable(client)
    ntfy = add_channel(client, "ntfy")
    telegram = add_channel(client, "telegram")
    http.responses["https://api.telegram.org"] = httpx.ConnectError("refused")
    result = send_note(client, alert_note())
    assert result["status"] == "partial"
    failed = next(r for r in result["results"] if r["channel_id"] == telegram["id"])
    assert failed["ok"] is False and "Could not connect to api.telegram.org" in failed["error"]

    channels = {c["id"]: c for c in client.get("/api/notifications/channels").json()}
    assert channels[ntfy["id"]]["last_sent_at"] and channels[ntfy["id"]]["last_error"] == ""
    assert channels[telegram["id"]]["last_sent_at"] is None
    assert "Could not connect" in channels[telegram["id"]]["last_error"]

    http.responses["https://ntfy.sh"] = httpx.ReadTimeout("slow")
    http.responses["https://api.telegram.org"] = httpx.Response(302, headers={"Location": "https://elsewhere.example"})
    result = send_note(client, alert_note())
    assert result["status"] == "failed"
    errors = sorted(r["error"] for r in result["results"])
    assert errors[0].startswith("Timed out after 10 s")
    assert "redirect" in errors[1]


def test_unexpected_channel_errors_never_raise(client, http):
    enable(client)
    add_channel(client, "ntfy")
    http.responses["https://ntfy.sh"] = RuntimeError("bug")
    result = send_note(client, alert_note())
    assert result["status"] == "failed"
    assert result["results"][0]["error"] == "Unexpected error (RuntimeError)"


def test_single_channel_test_ignores_switches(client, http):
    channel = add_channel(client, "ntfy", enabled=False)
    add_channel(client, "telegram")
    result = client.post(f"/api/notifications/channels/{channel['id']}/test").json()
    assert result["event"] == "test" and result["status"] == "sent"
    assert [r["channel_id"] for r in result["results"]] == [channel["id"]]
    assert http.calls[0]["url"] == "https://ntfy.sh"
    assert client.post("/api/notifications/channels/999/test").status_code == 404


def test_channel_crud(client, http):
    channel = add_channel(client, "slack", name="  Trading desk  ", events=["news", "risk", "news"])
    assert channel["name"] == "Trading desk" and channel["events"] == ["risk", "news"]
    assert channel["summary"] == "Webhook to hooks.slack.com"
    updated = client.put(f"/api/notifications/channels/{channel['id']}", json={"enabled": False, "events": []}).json()
    assert updated["enabled"] is False and updated["events"] == []
    assert (
        client.put(f"/api/notifications/channels/{channel['id']}", json={"config": {"webhook_url": "nope"}}).status_code
        == 422
    )
    assert client.delete(f"/api/notifications/channels/{channel['id']}").status_code == 204
    assert client.get("/api/notifications/channels").json() == []
    assert client.get("/api/notifications/settings").json()["channels_count"] == 0


# ------------------------------------------------------------------ history


def test_log_listing_and_clearing(client, http):
    enable(client)
    add_channel(client, "ntfy")
    for index in range(3):
        send_note(client, alert_note(title=f"Alert {index}"))
    rows = client.get("/api/notifications/log?limit=2").json()
    assert [row["title"] for row in rows] == ["Alert 2", "Alert 1"]
    assert rows[0]["event_label"] == "Strategy alerts" and rows[0]["url"] == "/#/alerts?id=3"
    assert client.get("/api/notifications/log?limit=500").status_code == 422
    assert client.delete("/api/notifications/log").status_code == 204
    assert log_rows(client) == []


def test_note_data_is_stored_json_safe(client, http):
    enable(client)
    add_channel(client, "webhook")
    when = now_utc()
    send_note(client, alert_note(data={"at": when, "levels": (1.0, 2.0)}))
    assert log_rows(client)[0]["data"] == {"at": str(when), "levels": [1.0, 2.0]}
    assert http.json()["data"]["at"] == str(when)


# ----------------------------------------------------------------- watchers


def risk_log(client):
    return [row for row in log_rows(client) if row["event"] == "risk"]


def test_risk_watch_notifies_once_per_code_per_day(client, http):
    add_channel(client, "ntfy")
    scheduler = client.app.state.scheduler
    assert client.post("/api/killswitch", json={"hours": 2, "reason": "tilt"}).status_code == 200

    scheduler.run_now("risk-watch")  # notifications off: nothing happens
    assert risk_log(client) == []

    enable(client)
    job = scheduler.run_now("risk-watch")
    assert job.last_error == "" and job.last_result == {"sent": 1}
    scheduler.run_now("risk-watch")
    rows = risk_log(client)
    assert len(rows) == 1
    assert rows[0]["data"]["code"] == "KILL_SWITCH" and rows[0]["title"] == "Kill switch is on"
    assert http.json()["priority"] == 5  # blocking events are high priority
    assert http.json()["tags"] == ["warning"]


def test_risk_notes_cover_warning_state_and_prop_codes():
    status = {
        "currency": "USD",
        "state": "warning",
        "loss_used": 240.0,
        "daily_loss_limit": 300.0,
        "loss_used_ratio": 0.8,
        "reasons": [
            {"code": "CHECKLIST_INCOMPLETE", "message": "Complete the checklist", "severity": "block"},
            {"code": "PROP_DAILY_LOSS", "message": "Prop daily loss hit", "severity": "block"},
            {"code": "PROP_CHALLENGE_ENDED", "message": "Challenge ended", "severity": "warn"},
        ],
    }
    notes = notify.risk_notes(status)
    assert [n.data["code"] for n in notes] == ["PROP_DAILY_LOSS", "PROP_CHALLENGE_ENDED", "DAILY_LOSS_WARNING"]
    assert [n.priority for n in notes] == ["high", "default", "default"]
    assert notes[0].url == "/#/prop" and notes[2].url == "/#/dashboard"
    assert "80% of today's loss limit (240.00 USD of 300.00 USD)" in notes[2].body


def add_news(client, minutes, **overrides):
    body = {
        "time": (now_utc() + timedelta(minutes=minutes)).isoformat(),
        "title": "US CPI",
        "currency": "USD",
        "impact": "high",
    }
    body.update(overrides)
    response = client.post("/api/news", json=body)
    assert response.status_code == 201, response.text
    return response.json()


def test_news_reminder_is_sent_once_per_event(client, http):
    enable(client, news_reminder_min=15)
    add_channel(client, "ntfy")
    soon = add_news(client, 10)
    add_news(client, 60, title="FOMC minutes")  # outside the reminder window
    add_news(client, 5, title="Retail sales", impact="low")  # below the impact threshold
    add_news(client, 5, title="SNB rate decision", currency="CHF")  # no configured market trades CHF

    scheduler = client.app.state.scheduler
    scheduler.run_now("news-reminders")
    scheduler.run_now("news-reminders")
    rows = [row for row in log_rows(client) if row["event"] == "news"]
    assert len(rows) == 1
    assert rows[0]["data"]["news_event_id"] == soon["id"]
    assert rows[0]["title"].startswith("USD news in ") and rows[0]["title"].endswith("US CPI")
    assert "New entries get a warning from" in rows[0]["body"]
    assert rows[0]["url"] == "/#/premarket"


def test_news_reminders_off_when_minutes_zero(client, http):
    enable(client, news_reminder_min=0)
    add_channel(client, "ntfy")
    add_news(client, 5)
    assert client.app.state.scheduler.run_now("news-reminders").last_result == {"sent": 0}
    assert log_rows(client) == []


def test_jobs_endpoint_lists_the_watchers(client):
    jobs = client.get("/api/notifications/jobs").json()
    assert [job["name"] for job in jobs] == ["risk-watch", "news-reminders"]


def test_log_rows_store_channel_names(client, http):
    enable(client)
    add_channel(client, "ntfy", name="Pixel")
    send_note(client, alert_note())
    with client.app.state.session_factory() as db:
        row = db.query(NotificationLog).one()
        assert row.results[0]["name"] == "Pixel"
        assert db.query(NotificationChannel).one().last_error == ""
