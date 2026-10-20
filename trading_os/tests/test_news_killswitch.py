"""Economic-calendar guardrail, kill switch (cool-off) and the news API."""

import urllib.error
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

from trading_os.api import news as news_api
from trading_os.services import news

from .conftest import complete_checklist, now_utc, trade_payload, update_settings, yesterday_iso

NOW = datetime(2026, 10, 20, 14, 0)
GOLD = SimpleNamespace(symbol="XAUUSD", base_currency="XAU", quote_currency="USD")
EURUSD = SimpleNamespace(symbol="EURUSD", base_currency="EUR", quote_currency="USD")
GER40 = SimpleNamespace(symbol="GER40", base_currency=None, quote_currency="EUR")


def rules(**overrides):
    values = dict(
        news_mode="block",
        news_before_min=30,
        news_after_min=30,
        news_min_impact="high",
        news_match_currency=True,
        cooloff_until=None,
        cooloff_reason="",
    )
    values.update(overrides)
    return SimpleNamespace(**values)


def event(minutes=10, currency="USD", impact="high", before_min=None, after_min=None, title="CPI", event_id=1):
    return SimpleNamespace(
        id=event_id,
        time=NOW + timedelta(minutes=minutes),
        title=title,
        currency=currency,
        impact=impact,
        before_min=before_min,
        after_min=after_min,
    )


def codes(violations):
    return [(v.code, v.severity) for v in violations]


# --------------------------------------------------------------------------- pure rules


def test_relevance_by_market_currencies():
    assert news.affects_symbol("USD", "XAUUSD", GOLD)
    assert not news.affects_symbol("EUR", "XAUUSD", GOLD)
    assert news.affects_symbol("EUR", "EURUSD", EURUSD) and news.affects_symbol("USD", "EURUSD", EURUSD)
    assert not news.affects_symbol("GBP", "EURUSD", EURUSD)
    assert news.affects_symbol("EUR", "GER40", GER40)
    assert not news.affects_symbol("USD", "GER40", GER40)
    assert news.affects_symbol("ALL", "GER40", GER40)
    assert news.affects_symbol("JPY", "XAUUSD", GOLD, match_currency=False)
    # Unconfigured symbol: the currency must appear in the symbol text.
    assert news.affects_symbol("JPY", "GBPJPY") and not news.affects_symbol("USD", "GBPJPY")


def test_impact_threshold_and_per_event_window_override():
    assert news.impact_meets("high", "medium") and news.impact_meets("medium", "medium")
    assert not news.impact_meets("low", "medium")
    settings = rules()
    assert news.window_minutes(event(), settings) == (30, 30)
    short = event(minutes=10, before_min=5, after_min=60)
    assert news.window_minutes(short, settings) == (5, 60)
    assert not news.in_window(short, settings, NOW)  # release in 10 min, only 5 min restricted before
    assert news.in_window(short, settings, NOW + timedelta(minutes=70))
    assert news.event_window(short, settings) == (NOW + timedelta(minutes=5), NOW + timedelta(minutes=70))


def test_entry_violations_follow_mode_threshold_and_currency():
    events = [event(), event(currency="EUR", event_id=2), event(impact="medium", event_id=3)]
    found = news.entry_news_violations(events, rules(), symbol="XAUUSD", market=GOLD, entry_time=NOW)
    assert codes(found) == [("NEWS_WINDOW", "block")]
    assert found[0].message == "CPI (USD, high) at 14:10 - entries are restricted 30 min before to 30 min after."
    warn = news.entry_news_violations(events, rules(news_mode="warn"), symbol="XAUUSD", market=GOLD, entry_time=NOW)
    assert codes(warn) == [("NEWS_WINDOW", "warn")]
    assert (
        news.entry_news_violations(events, rules(news_mode="off"), symbol="XAUUSD", market=GOLD, entry_time=NOW) == []
    )
    loose = rules(news_min_impact="medium", news_match_currency=False)
    assert len(news.entry_news_violations(events, loose, symbol="XAUUSD", market=GOLD, entry_time=NOW)) == 3
    later = NOW + timedelta(minutes=41)  # 31 min after the release
    assert news.entry_news_violations(events, rules(), symbol="XAUUSD", market=GOLD, entry_time=later) == []


def test_status_windows_and_upcoming_list():
    events = [
        event(minutes=-45, event_id=1, title="Expired"),  # window ended 15 min ago
        event(minutes=-20, currency="JPY", event_id=2, title="BoJ"),  # any currency counts for the status
        event(minutes=90, impact="low", event_id=3, title="Low"),
        event(minutes=60 * 23, event_id=4, title="Tomorrow"),
        event(minutes=60 * 25, event_id=5, title="Too far"),
    ]
    settings = rules(news_mode="warn")
    assert codes(news.active_window_violations(events, settings, NOW)) == [("NEWS_WINDOW", "warn")]
    assert news.active_window_violations(events, rules(news_mode="off"), NOW) == []
    upcoming = news.upcoming(events, settings, NOW)
    assert [(item["title"], item["active"]) for item in upcoming] == [("BoJ", True), ("Tomorrow", False)]
    assert upcoming[0]["window_end"] == NOW + timedelta(minutes=10)


def test_cooloff_is_only_extended():
    settings = rules(cooloff_until=NOW + timedelta(hours=2), cooloff_reason="Tilt")
    assert news.cooloff_active(settings, NOW) and not news.cooloff_active(settings, NOW + timedelta(hours=2))
    violation = news.cooloff_violation(settings)
    assert (violation.code, violation.severity) == ("KILL_SWITCH", "block")
    assert violation.message == "Cool-off active until 20 Oct 2026 16:00 (Tilt). No new trades."
    assert news.extended_cooloff(NOW + timedelta(hours=30), NOW, 4) == NOW + timedelta(hours=30)
    assert news.extended_cooloff(NOW + timedelta(hours=3), NOW, 24) == NOW + timedelta(hours=24)
    assert news.extended_cooloff(None, NOW, 12) == NOW + timedelta(hours=12)


FF_FEED = [
    {"title": "CPI m/m", "country": "USD", "date": "2026-10-20T08:30:00-04:00", "impact": "High", "forecast": "0.3%"},
    {"title": "CPI m/m", "country": "USD", "date": "2026-10-20T08:30:00-04:00", "impact": "High"},  # duplicate
    {"title": "Bank Holiday", "country": "GBP", "date": "2026-10-20T00:00:00-04:00", "impact": "Holiday"},
    {
        "title": "German ZEW",
        "country": "EUR",
        "date": "2026-10-20T05:00:00-04:00",
        "impact": "Medium",
        "previous": "-1",
    },
    {"title": "OPEC Meeting", "country": "All", "date": "2026-10-21T04:00:00-04:00", "impact": "Low"},
    {"title": "Odd code", "country": "US Dollar", "date": "2026-10-20T08:30:00-04:00", "impact": "High"},
    {"title": "Bad date", "country": "USD", "date": "next tuesday", "impact": "High"},
    "not an object",
]


def test_parse_forexfactory_converts_to_account_time_and_filters():
    parsed = news.parse_forexfactory(FF_FEED, "Europe/Berlin")
    assert [(e["title"], e["currency"], e["impact"], e["time"]) for e in parsed] == [
        ("CPI m/m", "USD", "high", datetime(2026, 10, 20, 14, 30)),
        ("German ZEW", "EUR", "medium", datetime(2026, 10, 20, 11, 0)),
        ("OPEC Meeting", "ALL", "low", datetime(2026, 10, 21, 10, 0)),
    ]
    assert parsed[0]["notes"] == "Forecast 0.3%" and parsed[1]["notes"] == "Previous -1"
    assert news.parse_forexfactory({"unexpected": "shape"}, "UTC") == []


# --------------------------------------------------------------------------- API: guardrail


def add_event(client, minutes=10, **overrides):
    payload = {
        "time": (now_utc() + timedelta(minutes=minutes)).isoformat(),
        "title": "Non-Farm Payrolls (NFP)",
        "currency": "USD",
        "impact": "high",
    }
    payload.update(overrides)
    response = client.post("/api/news", json=payload)
    assert response.status_code == 201, response.text
    return response.json()


def test_high_impact_news_blocks_live_entry_in_block_mode(client):
    complete_checklist(client)
    update_settings(client, news_mode="block")
    add_event(client)
    add_event(client, title="ECB Rate Decision", currency="EUR")  # not a gold currency

    response = client.post("/api/trades", json=trade_payload())
    assert response.status_code == 423
    news_codes = [v for v in response.json()["violations"] if v["code"] == "NEWS_WINDOW"]
    assert len(news_codes) == 1 and news_codes[0]["severity"] == "block"
    assert news_codes[0]["message"].startswith("Non-Farm Payrolls (NFP) (USD, high) at ")

    update_settings(client, news_mode="warn")
    created = client.post("/api/trades", json=trade_payload())
    assert created.status_code == 201, created.text
    assert {"code": "NEWS_WINDOW", "severity": "warn"}.items() <= next(
        w for w in created.json()["warnings"] if w["code"] == "NEWS_WINDOW"
    ).items()

    update_settings(client, news_mode="off")
    created = client.post("/api/trades", json=trade_payload())
    assert "NEWS_WINDOW" not in {w["code"] for w in created.json()["warnings"]}


def test_status_reports_open_window_and_upcoming_news(client):
    update_settings(client, news_mode="block")
    current = add_event(client, minutes=10)
    add_event(client, minutes=180, title="FOMC Minutes")
    add_event(client, minutes=60, title="Minor data", impact="low")

    status = client.get("/api/risk/status").json()
    assert {"code": "NEWS_WINDOW", "severity": "block"}.items() <= next(
        r for r in status["reasons"] if r["code"] == "NEWS_WINDOW"
    ).items()
    assert status["can_trade"] is False
    upcoming = status["upcoming_news"]
    assert [(item["title"], item["active"]) for item in upcoming] == [
        ("Non-Farm Payrolls (NFP)", True),
        ("FOMC Minutes", False),
    ]
    assert upcoming[0]["id"] == current["id"] and upcoming[0]["window_end"] == current["window_end"]

    update_settings(client, news_mode="off")
    status = client.get("/api/risk/status").json()
    assert "NEWS_WINDOW" not in {r["code"] for r in status["reasons"]}
    assert len(status["upcoming_news"]) == 2


# --------------------------------------------------------------------------- API: kill switch


def test_kill_switch_blocks_live_entries_and_warns_on_logged_trades(client):
    complete_checklist(client)
    status = client.post("/api/killswitch", json={"hours": 24, "reason": "Tilt"}).json()
    until = datetime.fromisoformat(status["cooloff_until"])
    assert timedelta(hours=23, minutes=59) <= until - now_utc() <= timedelta(hours=24, minutes=1)
    assert status["cooloff_reason"] == "Tilt" and status["can_trade"] is False
    assert {"code": "KILL_SWITCH", "severity": "block"}.items() <= status["reasons"][0].items()

    blocked = client.post("/api/trades", json=trade_payload())
    assert blocked.status_code == 423
    assert "KILL_SWITCH" in {v["code"] for v in blocked.json()["violations"]}

    logged = client.post(
        "/api/trades",
        json=trade_payload(status="closed", entry_time=yesterday_iso(), exit_time=yesterday_iso(), exit_price=2660.0),
    )
    assert logged.status_code == 201, logged.text
    warning = next(v for v in logged.json()["trade"]["violations"] if v["code"] == "KILL_SWITCH")
    assert warning["severity"] == "warn" and "(Tilt)" in warning["message"]


def test_kill_switch_extends_but_never_shortens_and_release_needs_unlock(client):
    first = client.post("/api/killswitch", json={"hours": 48, "reason": "Loss streak"}).json()
    shorter = client.post("/api/killswitch", json={"hours": 4}).json()
    assert shorter["cooloff_until"] == first["cooloff_until"] and shorter["cooloff_reason"] == "Loss streak"
    longer = client.post("/api/killswitch", json={"hours": 72, "reason": "Revenge urge"}).json()
    assert longer["cooloff_until"] > first["cooloff_until"] and longer["cooloff_reason"] == "Revenge urge"

    # The settings form can neither shorten nor clear the cool-off.
    update_settings(client, cooloff_until=None, cooloff_reason="")
    assert client.get("/api/risk/status").json()["cooloff_until"] == longer["cooloff_until"]

    for attempt in ({}, {"confirm": ""}, {"confirm": "unlock"}, {"confirm": "yes"}):
        response = client.post("/api/killswitch/release", json=attempt)
        assert response.status_code == 400
        assert "UNLOCK" in response.json()["detail"]
    released = client.post("/api/killswitch/release", json={"confirm": "UNLOCK"}).json()
    assert released["cooloff_until"] is None and released["cooloff_reason"] == ""
    assert "KILL_SWITCH" not in {r["code"] for r in released["reasons"]}
    assert client.get("/api/settings").json()["cooloff_until"] is None


@pytest.mark.parametrize("body", [{"hours": 0}, {"hours": 73}, {"hours": 24, "reason": "x" * 201}])
def test_kill_switch_validation(client, body):
    assert client.post("/api/killswitch", json=body).status_code == 422


def test_partial_settings_update_keeps_news_and_position_defaults(client):
    update_settings(
        client,
        news_mode="block",
        news_before_min=15,
        news_after_min=45,
        news_min_impact="medium",
        news_match_currency=False,
        default_auto_be=False,
        default_trail_pips=12.5,
    )
    saved = client.put("/api/settings", json={"account_name": "Gold desk"}).json()
    assert saved["account_name"] == "Gold desk"
    assert (saved["news_mode"], saved["news_before_min"], saved["news_after_min"]) == ("block", 15, 45)
    assert (saved["news_min_impact"], saved["news_match_currency"]) == ("medium", False)
    assert (saved["default_auto_be"], saved["default_trail_pips"]) == (False, 12.5)


# --------------------------------------------------------------------------- API: news CRUD


def test_news_crud_and_effective_window(client):
    update_settings(client, news_before_min=20, news_after_min=40)
    created = add_event(client, minutes=120, currency="usd", before_min=5, after_min=None, notes="  Forecast 180K ")
    assert created["currency"] == "USD" and created["notes"] == "Forecast 180K"
    assert (created["before"], created["after"], created["active"], created["meets_threshold"]) == (5, 40, False, True)
    release = datetime.fromisoformat(created["time"])
    assert datetime.fromisoformat(created["window_start"]) == release - timedelta(minutes=5)
    assert datetime.fromisoformat(created["window_end"]) == release + timedelta(minutes=40)

    everyone = add_event(client, minutes=30, currency="all", title="G7 statement", impact="low")
    assert everyone["currency"] == "ALL" and everyone["meets_threshold"] is False

    listed = client.get("/api/news").json()
    assert [item["id"] for item in listed] == [everyone["id"], created["id"]]  # ordered by time

    old = add_event(client, minutes=-60 * 24 * 10, title="Old CPI")
    assert old["id"] not in {item["id"] for item in client.get("/api/news").json()}
    day = old["time"][:10]
    assert [item["id"] for item in client.get(f"/api/news?date_from={day}&date_to={day}").json()] == [old["id"]]
    assert client.get("/api/news?date_from=2026-10-10&date_to=2026-10-01").status_code == 422

    changed = client.put(f"/api/news/{created['id']}", json={**created, "impact": "medium", "before_min": None})
    assert changed.status_code == 200, changed.text
    assert (changed.json()["impact"], changed.json()["before"]) == ("medium", 20)

    assert client.delete(f"/api/news/{created['id']}").status_code == 204
    assert client.delete(f"/api/news/{created['id']}").status_code == 404
    assert client.put(f"/api/news/{created['id']}", json=created).status_code == 404


@pytest.mark.parametrize(
    "overrides",
    [
        {"title": ""},
        {"title": "   "},
        {"title": "x" * 121},
        {"currency": "US"},
        {"currency": "EURO"},
        {"currency": "U$D"},
        {"impact": "extreme"},
        {"before_min": -1},
        {"after_min": 241},
        {"time": "tomorrow"},
    ],
)
def test_news_validation(client, overrides):
    payload = {"time": now_utc().isoformat(), "title": "CPI", "currency": "USD", "impact": "high", **overrides}
    assert client.post("/api/news", json=payload).status_code == 422


def test_forexfactory_import_dedupes_and_reports_network_errors(client, monkeypatch):
    update_settings(client, timezone="Europe/Berlin", news_min_impact="medium")
    monkeypatch.setattr(news_api, "_fetch_forexfactory", lambda: FF_FEED)
    first = client.post("/api/news/import-forexfactory").json()
    assert first == {"fetched": 3, "imported": 2, "duplicates": 0, "below_threshold": 1}
    stored = client.get("/api/news?date_from=2026-10-20&date_to=2026-10-21").json()
    assert [(e["title"], e["time"]) for e in stored] == [
        ("German ZEW", "2026-10-20T11:00:00"),
        ("CPI m/m", "2026-10-20T14:30:00"),
    ]
    again = client.post("/api/news/import-forexfactory", json={"min_impact": "low"}).json()
    assert again == {"fetched": 3, "imported": 1, "duplicates": 2, "below_threshold": 0}

    def offline():
        raise urllib.error.URLError("network unreachable")

    monkeypatch.setattr(news_api, "_fetch_forexfactory", offline)
    response = client.post("/api/news/import-forexfactory")
    assert response.status_code == 502
    assert "network unreachable" in response.json()["detail"]
