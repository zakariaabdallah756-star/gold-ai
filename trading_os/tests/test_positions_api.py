"""API tests of position management: scale-outs, break-even, manual actions, undo, ledger P&L."""

import pytest

from .conftest import complete_checklist, trade_payload, update_settings, yesterday_iso

THREE_TARGETS = [{"price": 2660, "close_pct": 50}, {"price": 2665, "close_pct": 25}, {"price": 2670, "close_pct": 25}]


@pytest.fixture
def live(client):
    complete_checklist(client)
    update_settings(client, warn_outside_sessions=False)
    return client


def create(client, **overrides) -> dict:
    response = client.post("/api/trades", json=trade_payload(**overrides))
    assert response.status_code == 201, response.text
    return response.json()["trade"]


def scale_out_trade(client, **overrides) -> dict:
    fields = {"position_size": 0.3, "targets": THREE_TARGETS, "be_enabled": True, "fees": 3.0}
    fields.update(overrides)
    return create(client, **fields)


def post(client, path: str, body: dict, status: int = 200) -> dict:
    response = client.post(path, json=body)
    assert response.status_code == status, response.text
    return response.json()


def test_scale_out_flow_feeds_risk_status_and_analytics(live):
    trade = scale_out_trade(live)
    assert trade["remaining_size"] == 0.3 and trade["remaining_risk"] == 150.0
    assert live.get("/api/risk/status").json()["open_risk"] == 150.0

    # TP1: half the position closes at 2660 and the stop moves to break-even.
    body = post(live, f"/api/trades/{trade['id']}/price", {"price": 2661})
    assert [(e["kind"], e["lots"], e["pnl"]) for e in body["events"]] == [
        ("tp", 0.15, 150.0),
        ("breakeven", None, None),
    ]
    updated = body["trade"]
    assert updated["status"] == "open" and updated["managed"] is True and updated["pnl"] is None
    assert updated["remaining_size"] == 0.15 and updated["realized_pnl"] == 150.0
    assert updated["current_stop"] == 2650.0 and updated["risk_free"] is True and updated["remaining_risk"] == 0.0
    assert [t["hit"] for t in updated["targets"]] == [True, False, False]

    status = live.get("/api/risk/status").json()
    assert status["realized_pnl_today"] == 150.0 and status["current_equity"] == 10_150.0
    assert status["open_risk"] == 0.0

    listing = live.get("/api/positions").json()
    position = listing["positions"][0]
    assert position["unrealized_pnl"] == 165.0  # 0.15 lots * 11.0 * 100
    assert position["next_target"] == {"level": 2, "price": 2665.0, "close_pct": 25.0}
    assert position["stop_kind"] == "breakeven"
    assert listing["totals"] == {
        "open_positions": 1,
        "remaining_risk": 0.0,
        "unrealized_pnl": 165.0,
        "realized_open": 150.0,
        "risk_free_count": 1,
    }

    tp2 = post(live, f"/api/trades/{trade['id']}/price", {"high": 2666, "low": 2659})["events"]
    assert [(e["kind"], e["lots"], e["pnl"]) for e in tp2] == [("tp", 0.07, 105.0)]

    final = post(live, f"/api/trades/{trade['id']}/price", {"price": 2671})["trade"]
    assert final["status"] == "closed" and final["remaining_size"] == 0.0
    assert final["exit_price"] == pytest.approx((2660 * 0.15 + 2665 * 0.07 + 2670 * 0.08) / 0.3, abs=1e-6)
    assert final["realized_pnl"] == 415.0 and final["pnl"] == 412.0  # 150 + 105 + 160 - 3 fees
    assert final["r_multiple"] == round(412 / 150, 4) and final["outcome"] == "win"
    assert final["exit_time"] == final["events"][-1]["time"]

    status = live.get("/api/risk/status").json()
    assert status["realized_pnl_today"] == 412.0 and status["current_equity"] == 10_412.0
    assert status["open_trades"] == 0 and status["open_risk"] == 0.0
    summary = live.get("/api/analytics").json()["summary"]
    assert summary["trades"] == 1 and summary["net_pnl"] == 412.0
    assert live.get("/api/positions").json()["totals"]["open_positions"] == 0


def test_manual_partial_close_move_stop_and_close(live):
    trade = create(live, position_size=0.3, fees=2.0)  # single take profit at 2665
    partial = post(live, f"/api/trades/{trade['id']}/partial-close", {"lots": 0.1, "price": 2655, "note": "news"})
    assert [(e["kind"], e["lots"], e["pnl"], e["note"]) for e in partial["events"]] == [("partial", 0.1, 50.0, "news")]
    assert partial["trade"]["remaining_size"] == 0.2 and partial["trade"]["last_price"] == 2655.0
    assert partial["trade"]["remaining_risk"] == 100.0  # 0.2 lots still risk 5.0 each

    too_high = live.post(f"/api/trades/{trade['id']}/move-stop", json={"stop": 2656})
    assert too_high.status_code == 422 and "below the last price" in too_high.json()["detail"]
    moved = post(live, f"/api/trades/{trade['id']}/move-stop", {"stop": 2652})
    assert moved["events"][0]["stop_before"] == 2645.0 and moved["events"][0]["stop_after"] == 2652.0
    assert moved["trade"]["current_stop"] == 2652.0 and moved["trade"]["remaining_risk"] == 0.0
    assert live.get("/api/positions").json()["positions"][0]["stop_kind"] == "manual"

    oversized = live.post(f"/api/trades/{trade['id']}/partial-close", json={"lots": 0.5, "price": 2656})
    assert oversized.status_code == 422 and "Only 0.2 lots remain" in oversized.json()["detail"]

    closed = post(live, f"/api/trades/{trade['id']}/close", {"price": 2658})["trade"]
    assert closed["status"] == "closed" and closed["events"][-1]["kind"] == "close"
    assert closed["realized_pnl"] == 210.0 and closed["pnl"] == 208.0  # 50 + 0.2 * 8 * 100 - 2
    assert closed["exit_price"] == pytest.approx((2655 * 0.1 + 2658 * 0.2) / 0.3, abs=1e-6)
    assert live.get("/api/risk/status").json()["realized_pnl_today"] == 208.0


def test_partial_loss_counts_toward_the_daily_loss_limit(live):
    trade = create(live, position_size=0.3)
    post(live, f"/api/trades/{trade['id']}/partial-close", {"lots": 0.2, "price": 2640, "pnl": -320.0})
    status = live.get("/api/risk/status").json()
    assert status["realized_pnl_today"] == -320.0 and status["loss_used"] == 320.0
    assert status["state"] == "locked" and status["can_trade"] is False


def test_undo_only_the_latest_event(live):
    trade = scale_out_trade(live)
    events = post(live, f"/api/trades/{trade['id']}/price", {"price": 2661})["events"]
    tp1, breakeven = events

    older = live.delete(f"/api/trades/{trade['id']}/events/{tp1['id']}")
    assert older.status_code == 409 and "most recent" in older.json()["detail"]
    assert live.delete(f"/api/trades/{trade['id']}/events/999").status_code == 404

    undone = live.delete(f"/api/trades/{trade['id']}/events/{breakeven['id']}").json()["trade"]
    assert undone["current_stop"] == 2645.0 and undone["remaining_risk"] == 75.0
    assert undone["best_price"] == 2660.0 and undone["last_price"] == 2660.0  # rewound to the ledger
    undone = live.delete(f"/api/trades/{trade['id']}/events/{tp1['id']}").json()["trade"]
    assert undone["events"] == [] and undone["managed"] is False and undone["remaining_size"] == 0.3
    assert undone["realized_pnl"] == 0.0 and undone["best_price"] is None
    assert live.get("/api/risk/status").json()["realized_pnl_today"] == 0.0


def test_closed_position_accepts_only_undo_of_its_final_event(live):
    trade = create(live, position_size=0.2)
    closed = post(live, f"/api/trades/{trade['id']}/price", {"price": 2644})["trade"]
    assert closed["status"] == "closed" and closed["events"][-1]["note"] == "initial stop"
    assert closed["pnl"] == -100.0 and closed["outcome"] == "loss" and closed["r_multiple"] == -1.0

    for path, body in (("price", {"price": 2650}), ("partial-close", {"lots": 0.1, "price": 2650})):
        response = live.post(f"/api/trades/{trade['id']}/{path}", json=body)
        assert response.status_code == 409, response.text

    reopened = live.delete(f"/api/trades/{trade['id']}/events/{closed['events'][-1]['id']}").json()["trade"]
    assert reopened["status"] == "open" and reopened["pnl"] is None and reopened["exit_time"] is None


def test_managed_trade_edit_ignores_exit_fields_but_applies_metadata(live):
    trade = scale_out_trade(live)
    post(live, f"/api/trades/{trade['id']}/price", {"price": 2660})
    edit = trade_payload(
        entry_time=trade["entry_time"],
        position_size=0.3,
        targets=THREE_TARGETS,
        be_enabled=True,
        status="closed",
        exit_price=2700.0,
        pnl=999.0,
        fees=5.0,
        notes="scaled out per plan",
        mistakes=["Early exit"],
        entry_price=2649.0,
    )
    response = live.put(f"/api/trades/{trade['id']}", json=edit)
    assert response.status_code == 200, response.text
    saved = response.json()["trade"]
    assert saved["status"] == "open" and saved["exit_price"] is None and saved["pnl"] is None
    assert saved["notes"] == "scaled out per plan" and saved["mistakes"] == ["Early exit"] and saved["fees"] == 5.0
    assert saved["events"][0]["pnl"] == 165.0 and saved["realized_pnl"] == 165.0  # revalued from the new entry

    smaller = live.put(f"/api/trades/{trade['id']}", json={**edit, "position_size": 0.1})
    assert smaller.status_code == 422 and "already closed" in smaller.json()["detail"]
    flipped = {**edit, "direction": "short", "stop_loss": 2655.0, "targets": [], "take_profit": 2640.0}
    assert live.put(f"/api/trades/{trade['id']}", json=flipped).status_code == 422
    late = live.put(f"/api/trades/{trade['id']}", json={**edit, "entry_time": "2099-01-01T00:00:00"})
    assert late.status_code == 422 and "first position event" in late.json()["detail"]


def test_unknown_market_fills_need_manual_pnl(live):
    trade = create(live, symbol="FOO", position_size=0.3, risk_amount=90.0, fees=1.0)
    assert trade["remaining_risk"] == 90.0

    priced = live.post(f"/api/trades/{trade['id']}/price", json={"price": 2655})
    assert priced.status_code == 422 and "not in your markets" in priced.json()["detail"]
    no_pnl = live.post(f"/api/trades/{trade['id']}/partial-close", json={"lots": 0.1, "price": 2655})
    assert no_pnl.status_code == 422 and "P&L" in no_pnl.json()["detail"]
    assert live.get(f"/api/trades/{trade['id']}").json()["events"] == []  # nothing half-saved

    partial = post(live, f"/api/trades/{trade['id']}/partial-close", {"lots": 0.1, "price": 2655, "pnl": 40.0})
    assert partial["events"][0]["pnl_manual"] is True and partial["trade"]["remaining_risk"] == 60.0
    closed = post(live, f"/api/trades/{trade['id']}/close", {"price": 2660, "pnl": 120.0})["trade"]
    assert closed["status"] == "closed" and closed["pnl"] == 159.0 and closed["pnl_manual"] is True


def test_symbol_price_update_applies_to_every_open_trade(live):
    near = create(live, position_size=0.1, stop_loss=2648.0, take_profit=2660.0)
    far = create(live, position_size=0.1, stop_loss=2640.0, entry_time=yesterday_iso(), take_profit=2680.0)
    sizeless = create(live, position_size=None, risk_amount=50.0, entry_time=yesterday_iso())
    other = create(live, symbol="EURUSD", entry_price=1.08, stop_loss=1.075, take_profit=1.095, position_size=0.1)

    body = post(live, "/api/positions/price", {"symbol": "xauusd", "high": 2651, "low": 2647.5})
    results = {r["trade_id"]: r for r in body["results"]}
    assert set(results) == {near["id"], far["id"], sizeless["id"]}
    assert [e["kind"] for e in results[near["id"]]["events"]] == ["stop"]
    assert results[far["id"]]["events"] == [] and results[sizeless["id"]]["skipped"] == "No position size set"
    trades = {t["id"]: t for t in body["trades"]}
    assert trades[near["id"]]["status"] == "closed" and trades[near["id"]]["pnl"] == -20.0
    assert trades[far["id"]]["worst_price"] == 2647.5 and trades[far["id"]]["best_price"] == 2651.0
    assert live.get(f"/api/trades/{other['id']}").json()["last_price"] is None

    unknown = live.post("/api/positions/price", json={"symbol": "FOO", "price": 1})
    assert unknown.status_code == 422
    invalid = live.post("/api/positions/price", json={"symbol": "XAUUSD", "high": 1, "low": 2})
    assert invalid.status_code == 422 and "below low" in invalid.json()["detail"]


def test_trailing_stop_and_event_time_rules(live):
    trade = create(
        live,
        position_size=0.2,
        take_profit=2680.0,
        trail_enabled=True,
        trail_distance_pips=30,
        trail_trigger="immediate",
    )
    early = live.post(f"/api/trades/{trade['id']}/price", json={"price": 2655, "time": "2000-01-01T00:00:00"})
    assert early.status_code == 422 and "before the entry" in early.json()["detail"]

    trailed = post(live, f"/api/trades/{trade['id']}/price", {"price": 2656})["trade"]
    assert trailed["current_stop"] == 2653.0 and trailed["risk_free"] is True and trailed["managed"] is False
    assert live.get("/api/positions").json()["positions"][0]["stop_kind"] == "trail"
    stopped = post(live, f"/api/trades/{trade['id']}/price", {"price": 2652})["trade"]
    assert stopped["status"] == "closed" and stopped["exit_price"] == 2653.0 and stopped["pnl"] == 60.0
    assert stopped["events"][-1]["note"] == "trailing stop"


def test_trade_without_position_size_cannot_record_fills(live):
    trade = create(live, position_size=None, risk_amount=100.0)
    for path, body in (("price", {"price": 2655}), ("close", {"price": 2655, "pnl": 10.0})):
        response = live.post(f"/api/trades/{trade['id']}/{path}", json=body)
        assert response.status_code == 422 and "position size" in response.json()["detail"]
    moved = post(live, f"/api/trades/{trade['id']}/move-stop", {"stop": 2647})["trade"]
    assert moved["current_stop"] == 2647.0 and moved["remaining_risk"] == 60.0  # 100 * 3/5 of the stop distance
    assert live.post("/api/trades/999/close", json={"price": 1}).status_code == 404


def test_legacy_closing_through_the_trade_form_still_works(live):
    trade = create(live, position_size=0.2)
    closing = trade_payload(entry_time=trade["entry_time"], status="closed", exit_price=2660.0, position_size=0.2)
    saved = live.put(f"/api/trades/{trade['id']}", json=closing).json()["trade"]
    assert saved["managed"] is False and saved["pnl"] == 200.0 and saved["remaining_size"] == 0.0
    assert saved["remaining_risk"] == 0.0
    assert live.get("/api/risk/status").json()["realized_pnl_today"] == 200.0


def test_realized_pnl_is_attributed_to_fill_times(live):
    entry = yesterday_iso()  # yesterday 12:00
    fill_time = entry.replace("T12:00", "T13:00")
    legacy = trade_payload(entry_time=entry, status="closed", exit_price=2655.0, position_size=0.1, fees=1.0)
    assert live.post("/api/trades", json=legacy).status_code == 201  # +50 - 1 yesterday
    trade = create(live, entry_time=entry, position_size=0.3, fees=4.0)
    post(live, f"/api/trades/{trade['id']}/partial-close", {"lots": 0.1, "price": 2660, "time": fill_time})
    status = live.get("/api/risk/status").json()
    assert status["realized_pnl_today"] == 0.0 and status["current_equity"] == 10_149.0
    assert status["start_of_day_equity"] == 10_149.0

    post(live, f"/api/trades/{trade['id']}/close", {"price": 2640})  # -200 today, fees 4 at the close
    status = live.get("/api/risk/status").json()
    assert status["realized_pnl_today"] == -204.0 and status["current_equity"] == 9_945.0
    closed = live.get(f"/api/trades/{trade['id']}").json()
    assert closed["pnl"] == -104.0 and closed["outcome"] == "loss"


def test_short_scale_out_then_break_even_stop(live):
    trade = create(
        live,
        direction="short",
        stop_loss=2655.0,
        take_profit=None,
        position_size=0.2,
        targets=[{"price": 2640, "close_pct": 50}, {"price": 2630, "close_pct": 50}],
        be_enabled=True,
        be_offset_pips=10,
        fees=2.0,
    )
    tp1 = post(live, f"/api/trades/{trade['id']}/price", {"high": 2645, "low": 2639})["trade"]
    assert tp1["realized_pnl"] == 100.0 and tp1["remaining_size"] == 0.1
    assert tp1["current_stop"] == 2649.0 and tp1["risk_free"] is True  # entry - 10 pips

    closed = post(live, f"/api/trades/{trade['id']}/price", {"price": 2649.5})["trade"]
    assert closed["status"] == "closed" and closed["events"][-1]["note"] == "break-even stop"
    assert closed["realized_pnl"] == 110.0 and closed["pnl"] == 108.0  # +100 at TP1, +10 at 2649
    assert closed["exit_price"] == 2644.5 and closed["executed_rr"] == 1.1


def test_undoing_a_mistyped_manual_partial_rewinds_price_tracking(live):
    """A partial close typed at 2950 instead of 2655 and undone must not feed the trailing stop."""
    trade = create(live, position_size=0.2, trail_enabled=True, trail_distance_pips=50, trail_trigger="immediate")
    post(live, f"/api/trades/{trade['id']}/price", {"price": 2652})
    body = post(live, f"/api/trades/{trade['id']}/partial-close", {"lots": 0.1, "price": 2950})
    mistyped = body["events"][0]
    undone = live.delete(f"/api/trades/{trade['id']}/events/{mistyped['id']}")
    assert undone.status_code == 200, undone.text
    after = undone.json()["trade"]
    assert after["best_price"] != 2950 and after["last_price"] != 2950

    body = post(live, f"/api/trades/{trade['id']}/price", {"price": 2653})
    trade = body["trade"]
    assert trade["status"] == "open" and trade["current_stop"] < 2653  # the trail follows real prices only
