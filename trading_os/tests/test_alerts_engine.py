"""Strategy alert engine: market data providers, setup evaluation, scanning, alerts and notifications."""

from datetime import datetime, timedelta, timezone

import httpx
import pytest
from sqlalchemy import func, select

from trading_os.models import AlertRule, Candle, NotificationLog, Strategy, StrategyAlert, Trade
from trading_os.services import alerts, detectors, market_data, notify
from trading_os.services.account import get_settings
from trading_os.services.market_data import Bar, MarketDataError
from trading_os.services.signal_parser import parse_signal

from .conftest import complete_checklist, now_utc


@pytest.fixture(autouse=True)
def fresh_cache():
    market_data.clear_cache()
    yield
    market_data.clear_cache()


@pytest.fixture
def db(client):
    with client.app.state.session_factory() as session:
        yield session


def g(x: float) -> float:
    """Scale the hand-built pattern prices to gold-like prices: 10.0 -> 2650."""
    return round(2650 + (x - 10) * 10, 2)


PREFIX = [(10.2, 10.5, 9.9, 10.2)] * 14
BASE = [
    (10.0, 10.4, 9.6, 10.2),
    (10.2, 10.6, 9.8, 10.4),
    (10.4, 10.8, 9.0, 10.0),  # swing low 2640.0 - the liquidity the signal candle sweeps
    (10.0, 10.5, 9.7, 10.3),
    (10.3, 10.7, 9.9, 10.5),
]
LONG_SIGNAL = (10.5, 10.6, 8.8, 10.4)  # sweeps 2640 to 2638, closes 2654: sweep + bullish pin bar
CONFLICT_SIGNAL = (10.6, 10.7, 8.9, 10.2)  # sweep of the lows (long) that is also a bearish engulfing (short)

SWEEP = {"type": "liquidity_sweep", "params": {"lookback": 5}, "confluence": "Liquidity sweep"}
PIN = {"type": "pin_bar", "params": {}, "confluence": "Pin bar"}
FIXED_LAST_OPEN = datetime(2026, 10, 20, 9, 45)  # signal candle closes 10:00 UTC = London session


def series(signal=LONG_SIGNAL, last_open=FIXED_LAST_OPEN, minutes=15):
    rows = PREFIX + BASE + [signal]
    start = last_open - timedelta(minutes=minutes * (len(rows) - 1))
    return [
        Bar(start + timedelta(minutes=minutes * i), g(o), g(h), g(lo), g(c)) for i, (o, h, lo, c) in enumerate(rows)
    ]


def live_last_open() -> datetime:
    now = now_utc()
    return now.replace(minute=now.minute - now.minute % 15, second=0) - timedelta(minutes=15)


def make_rule(db, **overrides) -> AlertRule:
    values = dict(
        name="Sweep reversal",
        symbols=["XAUUSD"],
        timeframe="M15",
        provider="demo",
        conditions=[SWEEP, PIN],
        stop_method="swing",
        targets_rr=[],
        cooldown_minutes=60,
    )
    values.update(overrides)
    rule = AlertRule(**values)
    db.add(rule)
    db.commit()
    return rule


def make_strategy(db, **overrides) -> Strategy:
    values = dict(name="Sweep strategy", status="live", required_confluences=[], allowed_sessions=[])
    values.update(overrides)
    strategy = Strategy(**values)
    db.add(strategy)
    db.commit()
    return strategy


def evaluate(db, rule, candles=None, symbol="XAUUSD", now=FIXED_LAST_OPEN + timedelta(minutes=16), **kwargs):
    return alerts.evaluate(db, get_settings(db), rule, symbol, candles or series(), now, **kwargs)


# --------------------------------------------------------------------------- evaluation


def test_setup_found_with_levels_size_and_diagnostics(db):
    ev = evaluate(db, make_rule(db))
    assert ev.found and ev.direction == "long"
    assert ev.candle_time == datetime(2026, 10, 20, 10, 0)
    assert ev.confluences == ["Liquidity sweep", "Pin bar"]
    assert [c.met for c in ev.conditions] == [True, True]
    levels = ev.levels
    assert (levels.entry, levels.stop_loss) == (2654.0, 2640.0)  # beyond the swing low
    assert levels.take_profits == [2682.0]  # one target at the account minimum 1:2
    assert levels.planned_rr == 2.0
    # 1% of 10,000 = 100 at risk over 140 pips x 10 per lot -> 0.0714 rounded down to 0.07 lots
    assert ev.lots == 0.07 and ev.risk_amount == 98.0 and ev.risk_pct == 1.0
    assert ev.session == "London"


def test_stop_methods_and_buffer(db):
    signal = evaluate(db, make_rule(db, stop_method="signal", stop_buffer_pips=5))
    assert signal.levels.stop_loss == 2637.5  # wick low 2638 minus 5 pips (0.1 each)
    assert "signal structure" in signal.levels.stop_note
    swing = evaluate(db, make_rule(db, stop_method="swing", stop_buffer_pips=5))
    assert swing.levels.stop_loss == 2639.5
    candles = series()
    atr = detectors.atr(candles, 14)[-1]
    by_atr = evaluate(db, make_rule(db, stop_method="atr", atr_multiple=2.0), candles)
    assert by_atr.levels.stop_loss == pytest.approx(round(2654.0 - 2 * atr, 2))


def test_targets_and_blended_reward_to_risk(db):
    ev = evaluate(db, make_rule(db, targets_rr=[1, 2, 3]))
    assert ev.levels.take_profits == [2668.0, 2682.0, 2696.0]
    assert ev.levels.close_pcts == [50.0, 25.0, 25.0]
    assert ev.levels.planned_rr == pytest.approx((1 * 50 + 2 * 25 + 3 * 25) / 100)
    strict = make_strategy(db, name="Strict", min_rr=3)
    default_target = evaluate(db, make_rule(db, strategy_id=strict.id))
    assert default_target.levels.take_profits == [2696.0]  # max(account 2R, strategy 3R)


def test_direction_consensus_conflict_and_filters(db):
    conflict = evaluate(
        db, make_rule(db, conditions=[SWEEP, {"type": "engulfing", "params": {}}]), series(CONFLICT_SIGNAL)
    )
    assert not conflict.found and "disagree" in conflict.reason
    short_only = evaluate(db, make_rule(db, direction="short"))
    assert not short_only.found and "only alerts short" in short_only.reason
    neutral = evaluate(db, make_rule(db, conditions=[{"type": "atr_filter", "params": {"min_atr_pct": 0.01}}]))
    assert not neutral.found and "neutral filters" in neutral.reason


def test_min_conditions(db):
    breakout = {"type": "range_breakout", "params": {"bars": 5}}
    all_needed = evaluate(db, make_rule(db, conditions=[SWEEP, PIN, breakout]))
    assert not all_needed.found and all_needed.reason == "2 of 3 conditions met (needs 3)."
    assert evaluate(db, make_rule(db, conditions=[SWEEP, PIN, breakout], min_conditions=2)).found


def test_copilot_missing_required_confluence_blocks_the_alert(db):
    strategy = make_strategy(db, required_confluences=["Liquidity sweep", "Break of structure"])
    ev = evaluate(db, make_rule(db, strategy_id=strategy.id), diagnostics=True)
    assert not ev.found and "Co-Pilot" in ev.reason
    assert "STRATEGY_REQUIRED_CONFLUENCES" in {v.code for v in ev.copilot}
    assert ev.precheck is not None  # the Test view still shows the guardrail pre-check
    strategy.required_confluences = ["Liquidity sweep"]
    db.commit()
    assert evaluate(db, make_rule(db, strategy_id=strategy.id)).found


def test_retired_strategies_never_alert_and_incubator_alerts_tagged(db):
    retired = make_strategy(db, name="Old", status="retired")
    ev = evaluate(db, make_rule(db, strategy_id=retired.id))
    assert not ev.found and "retired" in ev.reason
    incubator = make_strategy(db, name="New idea", status="incubator")
    ev = evaluate(db, make_rule(db, strategy_id=incubator.id))
    assert ev.found and ev.in_test
    assert [v.code for v in ev.copilot] == ["STRATEGY_INCUBATOR"]


def test_session_timeframe_and_min_confluence_filters(db):
    new_york = make_strategy(db, name="NY only", allowed_sessions=["New York"])
    ev = evaluate(db, make_rule(db, strategy_id=new_york.id))
    assert not ev.found and "STRATEGY_SESSION" in {v.code for v in ev.copilot}
    london = make_strategy(db, name="London only", allowed_sessions=["London"])
    assert evaluate(db, make_rule(db, strategy_id=london.id)).found
    hourly = make_strategy(db, name="Hourly", allowed_timeframes=["H1"])
    assert "STRATEGY_TIMEFRAME" in {v.code for v in evaluate(db, make_rule(db, strategy_id=hourly.id)).copilot}
    three = make_strategy(db, name="Three", min_confluences=3)
    ev = evaluate(db, make_rule(db, strategy_id=three.id))
    assert not ev.found and "CONFLUENCES_BELOW_MIN" in {v.code for v in ev.copilot}


def test_unconfigured_symbol_has_no_lot_size(db):
    ev = evaluate(db, make_rule(db, symbols=["TESTX"]), symbol="TESTX")
    assert ev.found and ev.lots is None
    assert "not one of your markets" in ev.sizing_note


def test_key_levels_come_from_todays_checklist(client, db):
    today = client.get("/api/checklist/today").json()["day"]
    client.put(
        f"/api/checklist/{today}", json={"checked_item_ids": [], "key_levels": "Support 2,640 / resistance 2700"}
    )
    rule = make_rule(db, conditions=[SWEEP, {"type": "key_level", "params": {"levels": [], "tolerance_atr": 0.5}}])
    candles = series(last_open=live_last_open())
    ev = alerts.evaluate(db, get_settings(db), rule, "XAUUSD", candles, now_utc())
    assert ev.conditions[1].met and "2640" in ev.conditions[1].explanation


# --------------------------------------------------------------------------- scanning & alerts


@pytest.fixture
def captured(monkeypatch):
    notes = []

    def fake_send(db, settings, note):
        notes.append(note)
        entry = NotificationLog(created_at=now_utc(), event=note.event, title=note.title, body=note.body)
        db.add(entry)
        db.flush()
        return entry

    monkeypatch.setattr(notify, "send", fake_send)
    return notes


def feed(monkeypatch, *, last_open=None, failing=()):
    def fake_fetch(db, settings, provider, symbol, timeframe, *, provider_symbols=None, now, limit=500):
        if symbol in failing:
            raise MarketDataError("Yahoo Finance did not answer in time - try again later.")
        return series(last_open=last_open or live_last_open())

    monkeypatch.setattr(market_data, "fetch_candles", fake_fetch)


def test_scan_records_alert_notifies_and_never_creates_trades(db, monkeypatch, captured):
    feed(monkeypatch)
    rule = make_rule(db, targets_rr=[1, 2])
    settings = get_settings(db)
    results = alerts.scan(db, settings, now_utc())
    assert [r["status"] for r in results] == ["alert"]
    alert = db.get(StrategyAlert, results[0]["alert_id"])
    assert (alert.symbol, alert.direction, alert.status) == ("XAUUSD", "long", "new")
    assert (alert.entry_price, alert.stop_loss, alert.take_profits) == (2654.0, 2640.0, [2668.0, 2682.0])
    assert alert.confluences == ["Liquidity sweep", "Pin bar"]
    assert alert.details["conditions"][0]["met"] is True
    assert db.scalar(select(func.count(Trade.id))) == 0

    (note,) = captured
    assert note.event == "strategy_alert" and note.priority == "high"
    assert note.title == "Sweep reversal · XAUUSD LONG · M15"
    assert "Confluences: Liquidity sweep, Pin bar" in note.body
    assert "Entry 2654.00 · SL 2640.00 · TP1 2668.00 · TP2 2682.00" in note.body
    assert "R:R 1:1.50 · 0.07 lots · risk 98.00 USD (1%)" in note.body
    assert note.body.endswith("Nothing was executed - the decision is yours.")
    assert note.url == f"/#/alerts?id={alert.id}"
    assert note.data == {
        "alert_id": alert.id,
        "symbol": "XAUUSD",
        "direction": "long",
        "entry": 2654.0,
        "stop_loss": 2640.0,
        "take_profits": [2668.0, 2682.0],
    }
    assert alert.notification_id is not None
    db.refresh(rule)
    assert rule.last_alert_at is not None and rule.last_checked_at is not None and rule.last_error == ""


def test_signal_text_round_trips_through_the_signal_parser(db, monkeypatch, captured):
    feed(monkeypatch)
    make_rule(db, targets_rr=[1, 2])
    (row,) = alerts.scan(db, get_settings(db), now_utc())
    alert = db.get(StrategyAlert, row["alert_id"])
    assert alert.signal_text == "XAUUSD BUY @ 2654.00\nSL 2640.00\nTP1 2668.00\nTP2 2682.00"
    parsed = parse_signal(alert.signal_text, ["XAUUSD"])
    assert (parsed.symbol, parsed.direction, parsed.entry, parsed.stop_loss) == ("XAUUSD", "long", 2654.0, 2640.0)
    assert parsed.take_profits == alert.take_profits and not parsed.warnings
    short = alerts.signal_text("EURUSD", "short", 1.08512, 1.0871, [1.08116], 5)
    eur = parse_signal(short, ["EURUSD"])
    assert (eur.direction, eur.entry, eur.stop_loss, eur.take_profits) == ("short", 1.08512, 1.0871, [1.08116])


def test_precheck_reports_the_checklist_until_it_is_done(client, db, monkeypatch, captured):
    rule = make_rule(db)
    feed(monkeypatch, last_open=live_last_open() - timedelta(minutes=15))
    (row,) = alerts.scan(db, get_settings(db), now_utc())
    alert = db.get(StrategyAlert, row["alert_id"])
    codes = {v["code"]: v["severity"] for v in alert.details["precheck"]}
    assert codes["CHECKLIST_INCOMPLETE"] == "block"
    assert "Pre-check: blocked - Complete today's pre-market checklist" in captured[-1].body

    complete_checklist(client)
    rule.cooldown_minutes = 0
    db.commit()
    feed(monkeypatch)
    (row,) = alerts.scan(db, get_settings(db), now_utc())
    alert = db.get(StrategyAlert, row["alert_id"])
    assert "CHECKLIST_INCOMPLETE" not in {v["code"] for v in alert.details["precheck"]}
    assert db.scalar(select(func.count(Trade.id))) == 0


def test_dedupe_per_candle_and_cooldown(db, monkeypatch, captured):
    rule = make_rule(db, cooldown_minutes=60)
    settings = get_settings(db)
    earlier = live_last_open() - timedelta(minutes=15)
    feed(monkeypatch, last_open=earlier)
    assert alerts.scan(db, settings, now_utc())[0]["status"] == "alert"
    duplicate = alerts.scan(db, settings, now_utc())[0]
    assert duplicate["status"] == "duplicate" and duplicate["alert_id"] is not None
    feed(monkeypatch)  # next candle, same pattern
    assert alerts.scan(db, settings, now_utc())[0]["status"] == "cooldown"
    rule.cooldown_minutes = 0
    db.commit()
    assert alerts.scan(db, settings, now_utc())[0]["status"] == "alert"
    assert db.scalar(select(func.count(StrategyAlert.id))) == 2
    assert len(captured) == 2


def test_rule_without_notify_records_silently(db, monkeypatch, captured):
    feed(monkeypatch)
    make_rule(db, notify=False)
    assert alerts.scan(db, get_settings(db), now_utc())[0]["status"] == "alert"
    assert captured == []


def test_one_failing_symbol_does_not_stop_the_others(db, monkeypatch, captured):
    feed(monkeypatch, failing={"XAGUSD"})
    rule = make_rule(db, symbols=["XAGUSD", "XAUUSD"])
    results = alerts.scan(db, get_settings(db), now_utc())
    assert [(r["symbol"], r["status"]) for r in results] == [("XAGUSD", "error"), ("XAUUSD", "alert")]
    assert "did not answer" in results[0]["message"]
    db.refresh(rule)
    assert rule.last_error.startswith("XAGUSD: Yahoo Finance did not answer")


def test_stale_data_does_not_alert(db, monkeypatch, captured):
    feed(monkeypatch, last_open=live_last_open() - timedelta(hours=3))
    make_rule(db)
    (row,) = alerts.scan(db, get_settings(db), now_utc())
    assert row["status"] == "no_setup" and "market closed or data delayed" in row["message"]


def test_scanner_job_honours_the_switch_but_manual_scans_always_run(client, db, monkeypatch, captured):
    feed(monkeypatch)
    make_rule(db)
    scheduler = client.app.state.scheduler
    job = scheduler.run_now("alert-scanner")
    assert job.last_result is None and db.scalar(select(func.count(StrategyAlert.id))) == 0

    response = client.put("/api/alerts/scanner", json={"enabled": True, "interval_sec": 300})
    assert response.status_code == 200 and response.json()["enabled"] is True
    job = scheduler.run_now("alert-scanner")
    assert job.last_error == ""
    assert job.last_result["manual"] is False and job.last_result["alerts"] == 1
    assert job.seconds() == 300

    client.put("/api/alerts/scanner", json={"enabled": False, "interval_sec": 60})
    kept = scheduler.run_now("alert-scanner").last_result
    assert kept["alerts"] == 1  # results of the last real scan are kept while the switch is off

    manual = client.post("/api/alerts/scan").json()
    assert manual["manual"] is True and manual["results"][0]["status"] == "duplicate"
    status = client.get("/api/alerts/scanner").json()
    assert status["last_manual"] is True and status["last_results"][0]["status"] == "duplicate"


def test_interval_is_clamped():
    assert alerts.clamp_interval(5) == 30 and alerts.clamp_interval(10_000) == 3600 and alerts.clamp_interval(90) == 90


# --------------------------------------------------------------------------- market data

NOW = datetime(2026, 10, 20, 9, 30)
EPOCH_0000 = int(datetime(2026, 10, 20, tzinfo=timezone.utc).timestamp())


def yahoo_payload(count=10):
    stamps = [EPOCH_0000 + 3600 * i for i in range(count)]
    opens = [100.0 + i for i in range(count)]
    quote = {
        "open": opens,
        "high": [o + 2 for o in opens],
        "low": [o - 1 for o in opens],
        "close": [o + 1 for o in opens],
        "volume": [10] * count,
    }
    quote["close"][5] = None  # Yahoo leaves gaps as null
    return {"chart": {"result": [{"meta": {"symbol": "GC=F"}, "timestamp": stamps, "indicators": {"quote": [quote]}}]}}


class FakeHttp:
    def __init__(self, status=200, payload=None, error=None):
        self.status, self.payload, self.error = status, payload, error
        self.calls = []

    def __call__(self, url, params, headers=None):
        self.calls.append({"url": url, "params": params, "headers": headers or {}})
        if self.error:
            raise self.error
        return self.status, self.payload


def fetch(db, provider, symbol, timeframe, now=NOW, **kwargs):
    return market_data.fetch_candles(db, get_settings(db), provider, symbol, timeframe, now=now, **kwargs)


def test_yahoo_parses_closed_bars_and_maps_symbols(db, monkeypatch):
    http = FakeHttp(payload=yahoo_payload())
    monkeypatch.setattr(market_data, "_http_get", http)
    candles = fetch(db, "yahoo", "XAUUSD", "H1")
    assert http.calls[0]["url"].endswith("/v8/finance/chart/GC=F")
    assert http.calls[0]["params"]["interval"] == "60m"
    # 10 bars from 00:00; 05:00 has a gap and 09:00 is still forming at 09:30.
    assert [bar.time.hour for bar in candles] == [0, 1, 2, 3, 4, 6, 7, 8]
    assert candles[0] == Bar(datetime(2026, 10, 20, 0, 0), 100.0, 102.0, 99.0, 101.0, 10.0)


def test_yahoo_h4_is_aggregated_from_hourly_bars(db, monkeypatch):
    monkeypatch.setattr(market_data, "_http_get", FakeHttp(payload=yahoo_payload()))
    candles = fetch(db, "yahoo", "XAUUSD", "H4")
    # Buckets 00-04 and 04-08 are closed; 08-12 is still forming.
    assert [bar.time.hour for bar in candles] == [0, 4]
    first = candles[0]
    assert (first.open, first.high, first.low, first.close, first.volume) == (100.0, 105.0, 99.0, 104.0, 40.0)


def test_provider_errors_are_readable(db, monkeypatch):
    missing = {"chart": {"result": None, "error": {"code": "Not Found", "description": "No data found"}}}
    monkeypatch.setattr(market_data, "_http_get", FakeHttp(404, missing))
    with pytest.raises(MarketDataError, match="Unknown symbol GC=X"):
        fetch(db, "yahoo", "XAUUSD", "H1", provider_symbols={"XAUUSD": "GC=X"})
    monkeypatch.setattr(market_data, "_http_get", FakeHttp(error=httpx.ConnectTimeout("slow")))
    with pytest.raises(MarketDataError, match="Yahoo Finance did not answer in time"):
        fetch(db, "yahoo", "XAUUSD", "M15")
    monkeypatch.setattr(market_data, "_http_get", FakeHttp(error=httpx.ConnectError("offline")))
    with pytest.raises(MarketDataError, match="Binance did not answer - check your internet connection"):
        fetch(db, "binance", "BTCUSD", "M15")
    monkeypatch.setattr(market_data, "_http_get", FakeHttp(429, {}))
    with pytest.raises(MarketDataError, match="rate limiting"):
        fetch(db, "yahoo", "EURUSD", "M5")
    monkeypatch.setattr(market_data, "_http_get", FakeHttp(400, {"code": -1121, "msg": "Invalid symbol."}))
    with pytest.raises(MarketDataError, match="Unknown symbol FOOUSDT on Binance"):
        fetch(db, "binance", "FOOUSD", "M15")


def kline(minute, o):
    start = EPOCH_0000 + 9 * 3600 + minute * 60
    return [start * 1000, str(o), str(o + 2), str(o - 1), str(o + 1), "12.5", (start + 899) * 1000, "0", 5]


def test_binance_klines(db, monkeypatch):
    http = FakeHttp(payload=[kline(-15, 60000), kline(0, 60010), kline(15, 60020)])
    monkeypatch.setattr(market_data, "_http_get", http)
    candles = fetch(db, "binance", "BTCUSD", "M15", now=datetime(2026, 10, 20, 9, 20))
    assert http.calls[0]["params"] == {"symbol": "BTCUSDT", "interval": "15m", "limit": 500}
    assert [bar.time for bar in candles] == [datetime(2026, 10, 20, 8, 45), datetime(2026, 10, 20, 9, 0)]
    assert candles[-1].close == 60011.0 and candles[-1].volume == 12.5


def test_twelvedata_needs_a_key_sent_as_header(db, monkeypatch):
    with pytest.raises(MarketDataError, match="Twelve Data API key missing"):
        fetch(db, "twelvedata", "XAUUSD", "M15")
    settings = get_settings(db)
    settings.market_api_keys = {"twelvedata": "td-secret-1234"}
    db.commit()
    values = [
        {"datetime": f"2026-10-20 {t}", "open": "2650", "high": "2652", "low": "2649", "close": "2651"}
        for t in ("09:15:00", "09:00:00", "08:45:00")
    ]
    http = FakeHttp(payload={"meta": {}, "values": values, "status": "ok"})
    monkeypatch.setattr(market_data, "_http_get", http)
    candles = fetch(db, "twelvedata", "XAUUSD", "M15", now=datetime(2026, 10, 20, 9, 20))
    call = http.calls[0]
    assert call["params"]["symbol"] == "XAU/USD" and call["params"]["interval"] == "15min"
    assert call["headers"] == {"Authorization": "apikey td-secret-1234"}
    assert "td-secret-1234" not in str(call["params"])
    assert [bar.time.strftime("%H:%M") for bar in candles] == ["08:45", "09:00"]

    market_data.clear_cache()
    bad_key = {"code": 401, "message": "**apikey** parameter is incorrect", "status": "error"}
    monkeypatch.setattr(market_data, "_http_get", FakeHttp(401, bad_key))
    with pytest.raises(MarketDataError, match="rejected the API key"):
        fetch(db, "twelvedata", "XAUUSD", "M15")


def test_cache_fetches_each_series_once_per_closed_bar(db, monkeypatch):
    http = FakeHttp(payload=yahoo_payload())
    monkeypatch.setattr(market_data, "_http_get", http)
    fetch(db, "yahoo", "XAUUSD", "H1")
    fetch(db, "yahoo", "XAUUSD", "H1", now=NOW + timedelta(minutes=20))
    assert len(http.calls) == 1
    fetch(db, "yahoo", "XAUUSD", "H1", now=NOW + timedelta(minutes=31))  # the 09:00 bar has closed
    assert len(http.calls) == 2


def test_demo_provider_is_deterministic_and_closed_only(db):
    now = datetime(2026, 10, 20, 9, 37, 12)
    first = fetch(db, "demo", "XAUUSD", "M15", now=now)
    assert first == fetch(db, "demo", "XAUUSD", "M15", now=now)
    assert len(first) == 500
    assert first[-1].time == datetime(2026, 10, 20, 9, 15)  # 09:30 is still forming at 09:37
    later = fetch(db, "demo", "XAUUSD", "M15", now=now + timedelta(hours=2))
    overlap = {bar.time: bar for bar in later}
    assert all(overlap[bar.time] == bar for bar in first if bar.time in overlap)
    assert all(2000 < bar.low <= bar.high < 3300 for bar in first)
    eur = fetch(db, "demo", "EURUSD", "H1", now=now)
    assert 0.8 < eur[-1].close < 1.4
    assert market_data.PROVIDERS["demo"].label == "Demo data (synthetic) - not real prices"


def test_push_provider_reads_stored_candles(db):
    for minute in (45, 60, 75):
        start = datetime(2026, 10, 20, 8, 0) + timedelta(minutes=minute)
        db.add(Candle(symbol="XAUUSD", timeframe="M15", time=start, open=1, high=2, low=0.5, close=1.5))
    db.commit()
    candles = fetch(db, "push", "XAUUSD", "M15", now=datetime(2026, 10, 20, 9, 20))
    assert [bar.time.strftime("%H:%M") for bar in candles] == ["08:45", "09:00"]
    with pytest.raises(MarketDataError, match="No candles pushed yet for EURUSD M15"):
        fetch(db, "push", "EURUSD", "M15")


# --------------------------------------------------------------------------- review fixes


def test_yahoo_h4_bucket_waits_for_its_last_hourly_bar(db, monkeypatch):
    # US index hours start at :30, so the 12:00 UTC bucket holds the 15:30-16:30 bar.
    stamps = [EPOCH_0000 + int((13.5 + i) * 3600) for i in range(3)]
    quote = {"open": [5800.0] * 3, "high": [5830.0] * 3, "low": [5790.0] * 3, "close": [5821.0] * 3, "volume": [1] * 3}
    payload = {"chart": {"result": [{"timestamp": stamps, "indicators": {"quote": [quote]}}]}}
    monkeypatch.setattr(market_data, "_http_get", FakeHttp(payload=payload))
    with pytest.raises(MarketDataError, match="No closed H4 candles"):
        fetch(db, "yahoo", "NAS100", "H4", now=datetime(2026, 10, 20, 16, 10))
    candles = fetch(db, "yahoo", "NAS100", "H4", now=datetime(2026, 10, 20, 16, 32))
    assert [bar.time.hour for bar in candles] == [12]


def test_closed_bars_are_judged_in_real_time_across_daylight_saving_changes(db, monkeypatch):
    settings = get_settings(db)
    settings.timezone = "Europe/London"
    db.commit()
    # Spring forward: the 00:00 UTC H4 bar ends at 04:00 UTC = 05:00 BST, not at 04:00 local.
    start = int(datetime(2026, 3, 29, tzinfo=timezone.utc).timestamp())
    klines = [[(start + k * 14400) * 1000, "100", "102", "99", "101", "1"] for k in (-1, 0)]
    monkeypatch.setattr(market_data, "_http_get", FakeHttp(payload=klines))
    early = fetch(db, "binance", "BTCUSD", "H4", now=datetime(2026, 3, 29, 4, 30))
    assert [bar.time for bar in early] == [datetime(2026, 3, 28, 20, 0)]
    market_data.clear_cache()
    late = fetch(db, "binance", "BTCUSD", "H4", now=datetime(2026, 3, 29, 5, 1))
    assert [bar.time for bar in late] == [datetime(2026, 3, 28, 20, 0), datetime(2026, 3, 29, 0, 0)]

    # Fall back: 00:00-01:45 UTC on 2026-10-25 shows 01:00-01:45 twice locally; real order is kept.
    settings.market_api_keys = {"twelvedata": "td-key-123456"}
    db.commit()
    values = [
        {"datetime": f"2026-10-25 {h:02d}:{m:02d}:00", "open": "1", "high": "2", "low": "0.5", "close": str(10 + k)}
        for k, (h, m) in enumerate((h, m) for h in (0, 1) for m in (0, 15, 30, 45))
    ]
    monkeypatch.setattr(market_data, "_http_get", FakeHttp(payload={"values": values[::-1], "status": "ok"}))
    candles = fetch(db, "twelvedata", "XAUUSD", "M15", now=datetime(2026, 10, 25, 3, 0))
    assert [bar.close for bar in candles] == [10 + k for k in range(8)]


def test_a_deleted_alert_is_not_sent_again_for_the_same_candle(db, monkeypatch, captured):
    feed(monkeypatch)
    rule = make_rule(db, cooldown_minutes=0)
    settings = get_settings(db)
    (row,) = alerts.scan(db, settings, now_utc())
    assert row["status"] == "alert"
    db.delete(db.get(StrategyAlert, row["alert_id"]))
    db.commit()
    (again,) = alerts.scan(db, settings, now_utc())
    assert again["status"] == "duplicate" and len(captured) == 1
    # The cool-down also survives the deleted alert.
    rule.cooldown_minutes = 60
    db.commit()
    feed(monkeypatch, last_open=live_last_open() + timedelta(minutes=15))
    assert alerts.scan(db, settings, now_utc() + timedelta(minutes=15))[0]["status"] == "cooldown"


def test_the_alert_is_saved_before_notifications_go_out(client, db, monkeypatch):
    seen = []

    def check_send(session, settings, note):
        with client.app.state.session_factory() as other:  # another connection sees the committed alert
            seen.append(other.scalar(select(func.count(StrategyAlert.id))))
        return None

    monkeypatch.setattr(notify, "send", check_send)
    feed(monkeypatch)
    make_rule(db)
    assert alerts.scan(db, get_settings(db), now_utc())[0]["status"] == "alert"
    assert seen == [1]


def test_targets_round_away_from_the_entry_and_levels_stay_positive(db):
    settings = get_settings(db)
    rule = AlertRule(stop_method="signal", stop_buffer_pips=0.0, targets_rr=[], atr_multiple=1.5)
    hint = alerts.ConditionCheck("x", "x", "x", True, "long", "", stop_hint=64700.0)
    bars = [Bar(datetime(2026, 10, 20, 9, 0), 65000.0, 65100.0, 64600.0, 65000.34)]
    levels, _ = alerts.compute_levels(settings, rule, None, "long", bars, [hint], 1.0)
    assert levels.entry == 65000.3 and levels.planned_rr >= 2.0
    rule.targets_rr = [1.5, 2.5]
    levels, _ = alerts.compute_levels(settings, rule, None, "long", bars, [hint], 1.0)
    risk = levels.entry - levels.stop_loss
    assert all(tp - levels.entry >= r * risk - 1e-9 for tp, r in zip(levels.take_profits, [1.5, 2.5], strict=True))

    short = alerts.ConditionCheck("x", "x", "x", True, "short", "", stop_hint=0.106)
    coin = [Bar(datetime(2026, 10, 20, 9, 0), 0.09, 0.1, 0.08, 0.082)]
    rule.targets_rr = [1, 3, 7]
    levels, reason = alerts.compute_levels(settings, rule, None, "short", coin, [short], 0.0001)
    assert levels is None and "below zero" in reason


def test_pip_guess_is_for_currency_pairs_only():
    assert alerts.pip_size_for(None, "EURUSD", 1.08) == 0.0001
    assert alerts.pip_size_for(None, "USDJPY", 150.0) == 0.01
    assert alerts.pip_size_for(None, "ETHUSD", 2600.0) == 0.1
    assert alerts.pip_size_for(None, "XAUUSD", 2650.0) == 0.1


def test_candles_missed_between_scans_are_caught_up(db, monkeypatch, captured):
    neutral = (10.4, 10.5, 10.3, 10.45)
    rows = PREFIX + BASE + [LONG_SIGNAL, neutral]
    last_open = live_last_open()
    start = last_open - timedelta(minutes=15 * (len(rows) - 1))
    full = [Bar(start + timedelta(minutes=15 * i), g(o), g(h), g(lo), g(c)) for i, (o, h, lo, c) in enumerate(rows)]
    available = {"bars": full[:-2]}
    monkeypatch.setattr(market_data, "fetch_candles", lambda *a, **k: available["bars"])
    make_rule(db)
    settings = get_settings(db)
    assert alerts.scan(db, settings, now_utc())[0]["status"] == "no_setup"
    available["bars"] = full  # two candles closed before the next scan: the signal and a quiet one
    (row,) = alerts.scan(db, settings, now_utc())
    assert row["status"] == "alert" and "candle closed" in row["message"]
    alert = db.get(StrategyAlert, row["alert_id"])
    assert alert.candle_time == full[-2].time + timedelta(minutes=15)
    assert captured[-1].body.startswith("Signal candle closed at")
    assert alerts.scan(db, settings, now_utc())[0]["status"] == "no_setup"  # nothing new, no repeat


def test_a_rule_deleted_during_a_scan_does_not_stop_the_others(client, db, monkeypatch, captured):
    first = make_rule(db, name="Deleted meanwhile")
    second = make_rule(db, name="Still here")
    first_id = first.id

    def fetch_and_delete(*args, **kwargs):
        with client.app.state.session_factory() as other:
            doomed = other.get(AlertRule, first_id)
            if doomed is not None:
                other.delete(doomed)
                other.commit()
        return series(last_open=live_last_open())

    monkeypatch.setattr(market_data, "fetch_candles", fetch_and_delete)
    results = alerts.scan(db, get_settings(db), now_utc())
    assert [(r["rule_name"], r["status"]) for r in results] == [("Still here", "alert")]
    db.expire_all()
    assert db.get(AlertRule, second.id).last_checked_at is not None


def test_test_view_names_the_linked_strategy_even_without_a_setup(db):
    strategy = make_strategy(db)
    rule = make_rule(db, strategy_id=strategy.id, conditions=[SWEEP, PIN, {"type": "rsi", "params": {}}])
    quiet = series(signal=(10.4, 10.5, 10.3, 10.45))
    result = evaluate(db, rule, quiet, diagnostics=True).to_dict()
    assert result["found"] is False and result["strategy"] == "Sweep strategy"


def test_saved_lookbacks_beyond_the_loaded_history_explain_themselves(db):
    rule = make_rule(db, conditions=[{"type": "ema_trend", "params": {"fast": 50, "slow": 600}}])
    ev = evaluate(db, rule)
    assert ev.found is False and ev.conditions[0].explanation.endswith("edit the rule.")
