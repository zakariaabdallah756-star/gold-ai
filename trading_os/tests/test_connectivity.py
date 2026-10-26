"""Connectivity status (v1.7): internet probe caching, broker bridge freshness, Telegram listener state.

No real network: the internet probe is injected. Bridge and Telegram state come from the database.
"""

from datetime import timedelta

from trading_os.models import TelegramListener, TradingAccount
from trading_os.services import connectivity
from trading_os.services.account import get_settings, local_now

from .test_execution import connect, new_account, paper_account, ready


def set_probe(client, online):
    client.app.state.internet_monitor = connectivity.InternetMonitor(probe=lambda: online)


def conn(client, refresh=True):
    return client.get(f"/api/status/connectivity{'?refresh=true' if refresh else ''}").json()


# ------------------------------------------------------------------------ internet


def test_internet_status_reflects_the_probe(client):
    set_probe(client, True)
    assert conn(client)["internet"]["online"] is True and conn(client)["level"] == "ok"
    set_probe(client, False)
    down = conn(client)
    assert down["internet"]["online"] is False and down["level"] == "down"


def test_the_probe_is_cached_between_checks(client):
    calls = {"n": 0}

    def probe():
        calls["n"] += 1
        return True

    client.app.state.internet_monitor = connectivity.InternetMonitor(probe=probe)
    conn(client, refresh=True)
    conn(client, refresh=False)  # within the cache window: no new probe
    conn(client, refresh=False)
    assert calls["n"] == 1
    conn(client, refresh=True)  # forced
    assert calls["n"] == 2


def test_a_probe_that_raises_is_treated_as_offline(client):
    def boom():
        raise OSError("no route")

    client.app.state.internet_monitor = connectivity.InternetMonitor(probe=boom)
    assert conn(client)["internet"]["online"] is False


# ------------------------------------------------------------------------- bridges


def test_only_connected_active_accounts_appear(client):
    set_probe(client, True)
    ready(client)
    new_account(client, name="Unlinked", kind="demo")  # no connection
    paper_account(client, name="Paper", kind="demo")
    bridges = conn(client)["bridges"]
    assert [b["name"] for b in bridges] == ["Paper"] and bridges[0]["state"] == "live"


def test_a_bridge_goes_stale_without_recent_contact(client):
    set_probe(client, True)
    ready(client)
    acct = new_account(client, name="MT5", kind="live")
    connect(client, acct["id"], "mt_bridge", {})
    factory = client.app.state.session_factory
    with factory() as db:
        settings = get_settings(db)
        row = db.get(TradingAccount, acct["id"])
        row.execution_enabled = True
        row.last_seen_at = local_now(settings) - timedelta(seconds=connectivity.BRIDGE_IDLE_SEC + 60)
        db.commit()
    bridge = conn(client)["bridges"][0]
    assert bridge["state"] == "stale" and bridge["sending"] is True and bridge["age_sec"] is not None
    assert conn(client)["level"] == "warn"  # a sending bridge with no recent data warns


def test_a_bridge_error_shows_and_warns(client):
    set_probe(client, True)
    ready(client)
    acct = new_account(client, name="MT5", kind="live")
    connect(client, acct["id"], "mt_bridge", {})
    factory = client.app.state.session_factory
    with factory() as db:
        row = db.get(TradingAccount, acct["id"])
        row.execution_enabled = True
        row.last_error = "EA not responding"
        db.commit()
    bridge = conn(client)["bridges"][0]
    assert bridge["state"] == "error" and bridge["last_error"] == "EA not responding"
    assert conn(client)["level"] == "warn"


def test_a_paper_account_is_always_live(client):
    set_probe(client, True)
    ready(client)
    paper_account(client, name="Paper", kind="demo")
    assert conn(client)["bridges"][0]["state"] == "live"


# ------------------------------------------------------------------------ telegram


def test_telegram_state_is_reported(client):
    set_probe(client, True)
    assert conn(client)["telegram"] == {"configured": False, "enabled": False, "ok": False, "state": "off"}
    factory = client.app.state.session_factory
    with factory() as db:
        settings = get_settings(db)
        row = TelegramListener(id=1, token_enc="x", enabled=True, last_ok_at=local_now(settings))
        db.add(row)
        db.commit()
    tg = conn(client)["telegram"]
    assert tg["configured"] is True and tg["state"] == "live" and tg["ok"] is True
    with factory() as db:
        db.get(TelegramListener, 1).last_error = "flood wait"
        db.commit()
    assert conn(client)["telegram"]["state"] == "error" and conn(client)["level"] == "warn"


# -------------------------------------------------------------------------- units


def test_overall_level_rules():
    assert connectivity.overall_level(False, [], {}) == "down"
    assert connectivity.overall_level(True, [], {}) == "ok"
    sending_live = [{"sending": True, "state": "live"}]
    assert connectivity.overall_level(True, sending_live, {}) == "ok"
    sending_stale = [{"sending": True, "state": "stale"}]
    assert connectivity.overall_level(True, sending_stale, {}) == "warn"
    # A stale bridge that is NOT sending does not warn.
    assert connectivity.overall_level(True, [{"sending": False, "state": "stale"}], {}) == "ok"
    assert connectivity.overall_level(True, [], {"state": "error"}) == "warn"
