from datetime import date, datetime, timedelta

import pytest

from trading_os.models import PropChallenge
from trading_os.services import prop

from .conftest import complete_checklist, now_utc, trade_payload, update_settings

EDITABLE = (
    "name",
    "firm",
    "preset",
    "phase",
    "account_size",
    "profit_target_pct",
    "max_daily_loss_pct",
    "max_total_drawdown_pct",
    "drawdown_mode",
    "min_trading_days",
    "max_calendar_days",
    "start_date",
    "enforce",
    "notes",
)


def editable(c: dict) -> dict:
    return {key: c[key] for key in EDITABLE}


def _day(offset: int) -> date:
    return now_utc().date() + timedelta(days=offset)


def _at(offset: int, hour: int = 10) -> str:
    return datetime.combine(_day(offset), datetime.min.time()).replace(hour=hour).isoformat()


def closed_trade(client, offset: int, pnl: float, hour: int = 10) -> dict:
    """A closed trade logged after the fact with a manual net P&L."""
    response = client.post(
        "/api/trades",
        json=trade_payload(
            status="closed",
            entry_time=_at(offset, hour),
            exit_time=_at(offset, hour + 1),
            exit_price=2660.0 if pnl > 0 else 2640.0,
            pnl=pnl,
        ),
    )
    assert response.status_code == 201, response.text
    return response.json()


def challenge(client, **overrides) -> dict:
    payload = {
        "name": "FTMO 10k",
        "firm": "FTMO",
        "preset": "ftmo_challenge",
        "phase": "phase1",
        "account_size": 10_000,
        "profit_target_pct": 10,
        "max_daily_loss_pct": 5,
        "max_total_drawdown_pct": 10,
        "drawdown_mode": "static",
        "min_trading_days": 2,
        "start_date": _day(-10).isoformat(),
        "enforce": True,
    }
    payload.update(overrides)
    response = client.post("/api/prop/challenges", json=payload)
    assert response.status_code == 201, response.text
    return response.json()


@pytest.fixture
def account(client):
    """Loose account rules so only the prop rules decide."""
    update_settings(client, max_daily_loss_pct=50, max_risk_pct=10)
    complete_checklist(client)
    return client


def test_presets_carry_the_verification_note(client):
    data = client.get("/api/prop/presets").json()
    keys = {p["key"] for p in data["presets"]}
    assert {"ftmo_challenge", "ftmo_verification", "ftmo_funded", "custom"} <= keys
    ftmo = next(p for p in data["presets"] if p["key"] == "ftmo_challenge")
    assert (ftmo["profit_target_pct"], ftmo["max_daily_loss_pct"], ftmo["max_total_drawdown_pct"]) == (10, 5, 10)
    assert all("verify" in p["note"].lower() for p in data["presets"])


def test_static_and_trailing_drawdown_floor():
    c = PropChallenge(account_size=10_000, max_total_drawdown_pct=10, drawdown_mode="static")
    assert prop.drawdown_floor(c, 10_800) == 9_000
    c.drawdown_mode = "trailing"
    assert prop.drawdown_floor(c, 10_000) == 9_000
    assert prop.drawdown_floor(c, 10_500) == 9_500
    assert prop.drawdown_floor(c, 12_000) == 10_000  # capped at the initial balance
    assert prop.drawdown_floor(c, 9_000) == 9_000  # never below the static floor


def test_metrics_progress_trading_days_and_trailing_history(account):
    client = account
    closed_trade(client, -5, 600)
    closed_trade(client, -3, -200)
    closed_trade(client, -3, 50, hour=14)
    closed_trade(client, -20, 999)  # before the challenge started: ignored
    c = challenge(client, drawdown_mode="trailing", min_trading_days=4)
    m = c["metrics"]
    assert m["balance"] == 10_450 and m["profit"] == 450 and m["profit_pct"] == 4.5
    assert m["target_amount"] == 1_000 and m["progress_pct"] == 45.0
    assert m["trading_days"] == 2 and m["min_trading_days"] == 4
    # The trailing floor follows the best end-of-day balance (10,600) minus 10% of 10k.
    assert m["drawdown_floor"] == 9_600 and m["total_remaining"] == 850
    assert c["status"] == "active" and c["active"] is True

    report = client.get(f"/api/prop/challenges/{c['id']}/report").json()
    days = {row["date"]: row for row in report["history"]}
    assert len(report["history"]) == 11
    assert days[_day(-5).isoformat()]["balance"] == 10_600
    assert days[_day(-3).isoformat()]["pnl"] == -150 and days[_day(-3).isoformat()]["daily_loss_used"] == 150
    assert days[_day(-3).isoformat()]["drawdown_floor"] == 9_600
    assert not any(row["breached"] for row in report["history"])


def test_daily_loss_follows_partial_fills_of_managed_trades(account):
    client = account
    c = challenge(client, start_date=_day(0).isoformat())
    trade = client.post(
        "/api/trades",
        json=trade_payload(
            position_size=1.0,
            take_profit=None,
            targets=[{"price": 2660, "close_pct": 50}, {"price": 2670, "close_pct": 50}],
        ),
    )
    assert trade.status_code == 201, trade.text
    trade_id = trade.json()["trade"]["id"]
    response = client.post(f"/api/trades/{trade_id}/partial-close", json={"lots": 0.5, "price": 2647.0})
    assert response.status_code == 200, response.text
    m = client.get(f"/api/prop/challenges/{c['id']}").json()["metrics"]
    assert m["daily_loss_used"] == 150  # 0.5 lots x 3 points x 100 at the fill time, trade still open
    assert m["open_risk"] == 250  # remaining 0.5 lots x 5 points to the 2645 stop
    assert m["daily_remaining"] == 500 - 150 - 250


def test_pass_fail_and_time_limit_evaluation(account):
    client = account
    closed_trade(client, -6, 700)
    closed_trade(client, -4, 400)
    passed = challenge(client, min_trading_days=2)
    assert passed["status"] == "passed" and "target" in passed["status_reason"].lower()
    assert passed["end_date"] == _day(0).isoformat()

    not_enough_days = challenge(client, name="Needs days", min_trading_days=4)
    assert not_enough_days["status"] == "active" and not_enough_days["metrics"]["target_reached"] is True

    closed_trade(client, -2, -900)
    failed = challenge(client, name="Static 5k", account_size=5_000, start_date=_day(-3).isoformat())
    assert failed["status"] == "failed"
    assert failed["status_reason"] == f"Daily loss limit breached on {_day(-2):%Y-%m-%d}"
    assert failed["end_date"] == _day(-2).isoformat()

    expired = challenge(
        client,
        name="Expired",
        start_date=_day(-40).isoformat(),
        max_calendar_days=30,
        profit_target_pct=50,
        max_daily_loss_pct=20,
        max_total_drawdown_pct=50,
    )
    assert expired["status"] == "failed" and expired["status_reason"] == "Time limit of 30 days elapsed"
    assert expired["end_date"] == _day(-11).isoformat()

    # Reopening re-evaluates: the breach is still in the history.
    reopened = client.put(f"/api/prop/challenges/{failed['id']}", json={**editable(failed), "status": "active"}).json()
    assert reopened["status"] == "failed"


def test_only_one_challenge_drives_prop_mode(client):
    first = challenge(client)
    second = challenge(client, name="Second")
    listed = {c["id"]: c["active"] for c in client.get("/api/prop/challenges").json()}
    assert listed == {first["id"]: False, second["id"]: True}
    client.post(f"/api/prop/challenges/{first['id']}/activate")
    listed = {c["id"]: c["active"] for c in client.get("/api/prop/challenges").json()}
    assert listed == {first["id"]: True, second["id"]: False}
    archived = client.post(f"/api/prop/challenges/{first['id']}/archive").json()
    assert archived["status"] == "archived" and archived["active"] is False
    assert client.post(f"/api/prop/challenges/{first['id']}/activate").status_code == 409
    assert client.post(f"/api/prop/challenges/{second['id']}/deactivate").json()["active"] is False
    assert client.get("/api/risk/status").json()["prop"] is None
    assert client.delete(f"/api/prop/challenges/{second['id']}").status_code == 204
    assert client.get(f"/api/prop/challenges/{second['id']}").status_code == 404


def test_no_active_challenge_means_no_prop_rules(account):
    client = account
    closed_trade(client, 0, -2_000, hour=0)
    response = client.post("/api/trades", json=trade_payload())
    assert response.status_code == 201
    assert not any(v["code"].startswith("PROP_") for v in response.json()["warnings"])


def test_enforced_challenge_blocks_entries_beyond_the_remaining_room(account):
    client = account
    challenge(client, start_date=_day(0).isoformat())
    closed_trade(client, 0, -450, hour=0)

    status = client.get("/api/risk/status").json()
    assert status["prop"]["daily_loss_used"] == 450 and status["prop"]["daily_remaining"] == 50
    assert status["can_trade"] is True

    blocked = client.post("/api/trades", json=trade_payload())  # risks 100 with 50 of daily room left
    assert blocked.status_code == 423
    assert "PROP_RISK_BUDGET" in blocked.text

    small = client.post("/api/trades", json=trade_payload(position_size=0.08))  # risks 40
    assert small.status_code == 201, small.text


def test_breach_blocks_with_enforce_and_warns_without(account):
    client = account
    c = challenge(client, start_date=_day(0).isoformat())
    closed_trade(client, 0, -600, hour=0)

    status = client.get("/api/risk/status").json()
    codes = {r["code"]: r["severity"] for r in status["reasons"]}
    assert codes["PROP_DAILY_LOSS"] == "block" and codes["PROP_CHALLENGE_ENDED"] == "warn"
    assert status["can_trade"] is False and status["prop"]["status"] == "failed"
    blocked = client.post("/api/trades", json=trade_payload(position_size=0.01))
    assert blocked.status_code == 423 and "PROP_DAILY_LOSS" in blocked.text

    client.put(f"/api/prop/challenges/{c['id']}", json={**editable(c), "enforce": False})
    allowed = client.post("/api/trades", json=trade_payload(position_size=0.01))
    assert allowed.status_code == 201, allowed.text
    warned = {v["code"]: v["severity"] for v in allowed.json()["warnings"]}
    assert warned["PROP_DAILY_LOSS"] == "warn"


def test_target_reached_warns_and_challenge_end_is_reported(account):
    client = account
    closed_trade(client, -1, 1_200)
    c = challenge(client, min_trading_days=3, start_date=_day(-2).isoformat())
    response = client.post("/api/trades", json=trade_payload())
    assert response.status_code == 201
    warning = next(v for v in response.json()["warnings"] if v["code"] == "PROP_TARGET_REACHED")
    assert warning["severity"] == "warn" and "more trading day" in warning["message"]
    # Yesterday plus today: 2 of the 3 required trading days.
    assert client.get(f"/api/prop/challenges/{c['id']}").json()["metrics"]["trading_days"] == 2


def test_profit_after_the_time_limit_does_not_pass_the_challenge(account):
    client = account
    closed_trade(client, -29, 500)  # day 2 of a 30-day window starting 30 days ago
    closed_trade(client, 0, 600)  # day 31: outside the window
    c = challenge(client, start_date=_day(-30).isoformat(), max_calendar_days=30, min_trading_days=1)
    assert c["status"] == "failed" and c["status_reason"] == "Time limit of 30 days elapsed"
    assert c["metrics"]["profit"] == 500
