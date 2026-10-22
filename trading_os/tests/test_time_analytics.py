from datetime import date, datetime
from types import SimpleNamespace

from trading_os.services import analytics, time_analytics

from .conftest import trade_payload, update_settings


def make_trade(entry, pnl, *, exit_time=None, r=None, mistakes=(), trade_id=1):
    return SimpleNamespace(
        id=trade_id,
        symbol="XAUUSD",
        direction="long",
        strategy="",
        session="",
        emotion="",
        timeframe="",
        confluences=[],
        mistakes=list(mistakes),
        pnl=pnl,
        fees=0.0,
        r_multiple=r,
        planned_rr=None,
        executed_rr=None,
        outcome="win" if pnl > 0 else "loss" if pnl < 0 else "breakeven",
        rule_compliant=None,
        entry_time=entry,
        exit_time=exit_time,
        close_time=exit_time or entry,
    )


# ------------------------------------------------------------------ heatmap


def test_heatmap_buckets_by_weekday_and_entry_hour():
    trades = [
        make_trade(datetime(2026, 1, 5, 9, 15), 100.0, r=2.0),  # Monday 09h
        make_trade(datetime(2026, 1, 5, 9, 50), -50.0, r=-1.0),  # Monday 09h
        # Wednesday 14h entry closed on Thursday: bucketed by its entry.
        make_trade(datetime(2026, 1, 7, 14, 0), 30.0, exit_time=datetime(2026, 1, 8, 10, 0)),
    ]
    result = time_analytics.heatmap(trades)

    assert [(c["weekday"], c["hour"]) for c in result["cells"]] == [(0, 9), (2, 14)]
    monday = result["cells"][0]
    assert (monday["trades"], monday["wins"], monday["losses"]) == (2, 1, 1)
    assert monday["win_rate"] == 50.0
    assert monday["net_pnl"] == 50.0
    assert monday["avg_r"] == 0.5
    assert result["cells"][1]["avg_r"] is None

    assert len(result["by_hour"]) == 24 and len(result["by_weekday"]) == 7
    assert result["by_hour"][9]["trades"] == 2
    assert result["by_hour"][10] == {
        "hour": 10,
        "trades": 0,
        "wins": 0,
        "losses": 0,
        "win_rate": None,
        "net_pnl": 0.0,
        "avg_r": None,
    }
    assert result["by_weekday"][0]["label"] == "Monday"
    assert [d["trades"] for d in result["by_weekday"]] == [2, 0, 1, 0, 0, 0, 0]


def test_heatmap_of_no_trades_keeps_the_marginals():
    result = time_analytics.heatmap([])
    assert result["cells"] == []
    assert sum(h["trades"] for h in result["by_hour"]) == 0
    assert [d["label"] for d in result["by_weekday"]][-1] == "Sunday"


# ------------------------------------------------------------------ calendar


def test_month_grid_covers_whole_weeks():
    # March 2026 starts on a Sunday and ends on a Tuesday.
    assert time_analytics.month_grid(date(2026, 3, 15)) == (
        date(2026, 3, 1),
        date(2026, 3, 31),
        date(2026, 2, 23),
        date(2026, 4, 5),
    )
    # June 2026 starts on a Monday: no leading days.
    first, last, start, end = time_analytics.month_grid(time_analytics.parse_month("2026-06"))
    assert start == first == date(2026, 6, 1)
    assert last == date(2026, 6, 30) and end == date(2026, 7, 5)


def test_calendar_month_days_weeks_and_totals():
    trades = [
        make_trade(datetime(2026, 2, 27, 10), 40.0),  # previous month, shown in the first week
        make_trade(datetime(2026, 3, 2, 9), 100.0),
        make_trade(datetime(2026, 3, 2, 15), -30.0),
        make_trade(datetime(2026, 3, 3, 11), -80.0),
        make_trade(datetime(2026, 3, 4, 11), 0.0),  # breakeven day: traded, neither green nor red
        make_trade(datetime(2026, 3, 9, 23), 250.0, exit_time=datetime(2026, 3, 10, 1)),  # closes on the 10th
        make_trade(datetime(2026, 4, 2, 10), -10.0),  # next month, shown in the last week
        make_trade(datetime(2026, 4, 20, 10), 999.0),  # outside the grid
    ]
    result = time_analytics.calendar_month(
        trades, date(2026, 3, 1), reviewed={date(2026, 3, 2)}, checklist_complete={date(2026, 3, 3)}
    )

    assert (result["month"], result["start"], result["end"]) == ("2026-03", "2026-02-23", "2026-04-05")
    days = {d["date"]: d for d in result["days"]}
    assert len(result["days"]) == 42 and len(result["weeks"]) == 6
    assert days["2026-02-23"]["in_month"] is False and days["2026-03-01"]["in_month"] is True
    assert days["2026-02-27"]["pnl"] == 40.0 and days["2026-02-27"]["in_month"] is False
    assert days["2026-03-02"] == {
        "date": "2026-03-02",
        "in_month": True,
        "pnl": 70.0,
        "trades": 2,
        "wins": 1,
        "losses": 1,
        "win_rate": 50.0,
        "partials": 0,
        "has_review": True,
        "checklist_complete": False,
    }
    assert days["2026-03-03"]["checklist_complete"] is True
    assert days["2026-03-09"]["trades"] == 0 and days["2026-03-10"]["pnl"] == 250.0
    assert days["2026-03-11"]["win_rate"] is None

    weeks = result["weeks"]
    assert [w["week_start"] for w in weeks][:2] == ["2026-02-23", "2026-03-02"]
    assert weeks[0]["pnl"] == 40.0
    assert (weeks[1]["pnl"], weeks[1]["trades"], weeks[1]["win_rate"]) == (-10.0, 4, 25.0)
    assert weeks[2]["pnl"] == 250.0
    assert weeks[-1]["pnl"] == -10.0

    total = result["month_total"]
    assert (total["pnl"], total["trades"], total["wins"], total["losses"]) == (240.0, 5, 2, 2)
    assert total["win_rate"] == 40.0
    assert (total["trading_days"], total["green_days"], total["red_days"]) == (4, 2, 1)
    assert total["best_day"] == {"date": "2026-03-10", "pnl": 250.0, "trades": 1}
    assert total["worst_day"] == {"date": "2026-03-03", "pnl": -80.0, "trades": 1}


def managed_trade(fills, pnl, *, fees=0.0, status="closed", trade_id=7, entry=None):
    """A scale-out: ``fills`` are (time, kind, lots, gross pnl) in ledger order."""
    events = [
        SimpleNamespace(id=index, time=when, kind=kind, lots=lots, pnl=gross, price=2650.0, target_level=None)
        for index, (when, kind, lots, gross) in enumerate(fills, start=1)
    ]
    entry = entry or fills[0][0]
    exit_time = fills[-1][0] if status == "closed" else None
    trade = make_trade(entry, pnl or 0.0, exit_time=exit_time, trade_id=trade_id)
    trade.__dict__.update(managed=True, status=status, events=events, pnl=pnl, fees=fees)
    if pnl is None:
        trade.outcome = None
    return trade


def test_partial_exits_are_booked_on_their_fill_day_across_months():
    scale_out = managed_trade(
        [(datetime(2026, 3, 31, 10), "tp", 0.1, 120.0), (datetime(2026, 4, 1, 9), "stop", 0.1, -50.0)],
        68.0,
        fees=2.0,
    )
    others = [make_trade(datetime(2026, 3, 30, 10), 50.0, trade_id=1)]

    march = time_analytics.calendar_month([scale_out, *others], date(2026, 3, 1))
    days = {d["date"]: d for d in march["days"]}
    assert (days["2026-03-31"]["pnl"], days["2026-03-31"]["trades"], days["2026-03-31"]["partials"]) == (120.0, 0, 1)
    assert days["2026-03-31"]["win_rate"] is None
    # The closing fill is booked on 1 April, net of the fees, and the win is counted there.
    assert (days["2026-04-01"]["pnl"], days["2026-04-01"]["trades"], days["2026-04-01"]["wins"]) == (-52.0, 1, 1)
    total = march["month_total"]
    assert (total["pnl"], total["trades"], total["partials"]) == (170.0, 1, 1)
    # A day with only a partial exit is a trading (green) day and can be the best day.
    assert (total["trading_days"], total["green_days"], total["red_days"]) == (2, 2, 0)
    assert total["best_day"] == {"date": "2026-03-31", "pnl": 120.0, "trades": 0}
    last_week = march["weeks"][-1]  # 30 Mar - 5 Apr holds both fills: the whole trade
    assert (last_week["pnl"], last_week["trades"], last_week["partials"]) == (118.0, 2, 1)

    april = time_analytics.calendar_month([scale_out, *others], date(2026, 4, 1))
    total = april["month_total"]
    assert (total["pnl"], total["trades"], total["wins"], total["partials"]) == (-52.0, 1, 1, 0)
    assert (total["red_days"], total["worst_day"]["date"]) == (1, "2026-04-01")
    assert next(d for d in april["days"] if d["date"] == "2026-03-31")["in_month"] is False


def test_open_trade_partials_count_in_the_calendar():
    still_open = managed_trade([(datetime(2026, 6, 3, 11), "partial", 0.1, -40.0)], None, status="open", fees=5.0)
    total = time_analytics.calendar_month([still_open], date(2026, 6, 1))["month_total"]
    assert (total["pnl"], total["trades"], total["partials"], total["red_days"]) == (-40.0, 0, 1, 1)
    assert total["win_rate"] is None and total["worst_day"]["pnl"] == -40.0


def test_calendar_month_without_red_or_green_days():
    result = time_analytics.calendar_month([], date(2026, 6, 1))
    total = result["month_total"]
    assert total["best_day"] is None and total["worst_day"] is None
    assert total["win_rate"] is None and total["pnl"] == 0.0
    assert all(not d["has_review"] for d in result["days"])


# ------------------------------------------------------------------ mistakes


def test_mistake_breakdown_and_cost_ordering():
    trades = [
        make_trade(datetime(2026, 1, 5, 9), 100.0, trade_id=1),
        make_trade(datetime(2026, 1, 6, 9), -200.0, mistakes=["FOMO entry", "Oversized"], trade_id=2),
        make_trade(datetime(2026, 1, 7, 9), -50.0, mistakes=["FOMO entry"], trade_id=3),
        make_trade(datetime(2026, 1, 8, 9), 30.0, mistakes=["Early exit"], trade_id=4),
    ]
    rows = {row["key"]: row for row in analytics.breakdowns(trades)["mistake"]}
    assert set(rows) == {"No mistakes", "FOMO entry", "Oversized", "Early exit"}
    assert (rows["FOMO entry"]["trades"], rows["FOMO entry"]["net_pnl"]) == (2, -250.0)
    assert rows["No mistakes"]["net_pnl"] == 100.0

    summary = analytics.summarize(trades, 10_000)
    assert (summary["mistake_free"]["trades"], summary["mistake_free"]["net_pnl"]) == (1, 100.0)
    assert (summary["with_mistakes"]["trades"], summary["with_mistakes"]["net_pnl"]) == (3, -220.0)
    assert [row["mistake"] for row in summary["mistake_cost"]] == ["FOMO entry", "Oversized", "Early exit"]
    assert summary["mistake_cost"][0] == {
        "mistake": "FOMO entry",
        "trades": 2,
        "net_pnl": -250.0,
        "avg_pnl": -125.0,
        "win_rate": 0.0,
    }


def test_trades_without_mistakes_attribute_count_as_mistake_free():
    trade = make_trade(datetime(2026, 1, 5, 9), 10.0)
    del trade.mistakes
    assert analytics.summarize([trade], 1000)["mistake_cost"] == []
    assert analytics.breakdowns([trade])["mistake"][0]["key"] == "No mistakes"


# ------------------------------------------------------------------ API


def closed_trade(client, entry, pnl, **extra):
    payload = trade_payload(status="closed", entry_time=entry, exit_price=2660.0, pnl=pnl, **extra)
    response = client.post("/api/trades", json=payload)
    assert response.status_code == 201, response.text
    return response.json()["trade"]


def test_analytics_time_heatmap_uses_account_local_time(client):
    update_settings(client, timezone="America/New_York")
    # 15:30 UTC on Wednesday 7 Jan 2026 is 10:30 in New York (UTC-5).
    closed_trade(client, "2026-01-07T15:30:00Z", 120.0, mistakes=["FOMO entry"])
    data = client.get("/api/analytics").json()

    heat = data["time_heatmap"]
    assert [(c["weekday"], c["hour"], c["trades"]) for c in heat["cells"]] == [(2, 10, 1)]
    assert heat["by_hour"][10]["net_pnl"] == 120.0
    assert heat["by_weekday"][2]["trades"] == 1
    assert data["summary"]["mistake_cost"][0]["mistake"] == "FOMO entry"
    assert data["summary"]["with_mistakes"]["trades"] == 1
    assert {row["key"] for row in data["breakdowns"]["mistake"]} == {"FOMO entry"}


def test_calendar_totals_match_api_trades(client):
    closed_trade(client, "2026-02-28T10:00:00", 20.0)  # Saturday before March: first grid week only
    closed_trade(client, "2026-03-02T10:00:00", 150.0)
    closed_trade(client, "2026-03-02T14:00:00", -50.0)
    closed_trade(client, "2026-03-12T09:00:00", -75.0)
    client.put("/api/reviews/2026-03-02", json={"discipline_rating": 4})

    calendar = client.get("/api/analytics/calendar", params={"month": "2026-03"}).json()
    total = calendar["month_total"]
    assert (total["pnl"], total["trades"], total["wins"], total["losses"]) == (25.0, 3, 1, 2)
    assert (total["green_days"], total["red_days"]) == (1, 1)
    assert total["best_day"]["date"] == "2026-03-02" and total["best_day"]["pnl"] == 100.0
    assert total["worst_day"]["date"] == "2026-03-12"
    assert round(sum(d["pnl"] for d in calendar["days"] if d["in_month"]), 2) == total["pnl"]
    assert round(sum(w["pnl"] for w in calendar["weeks"]), 2) == 45.0
    assert next(d for d in calendar["days"] if d["date"] == "2026-03-02")["has_review"] is True

    stats = client.get("/api/analytics", params={"date_from": "2026-03-01", "date_to": "2026-03-31"}).json()
    assert stats["summary"]["net_pnl"] == total["pnl"]

    today = client.get("/api/plan").json()["today"]
    assert client.get("/api/analytics/calendar").json()["month"] == today[:7]
    assert client.get("/api/analytics/calendar", params={"month": "2026-13"}).status_code == 422


def test_day_detail(client):
    late = closed_trade(client, "2026-03-02T14:00:00", -50.0)
    early = closed_trade(client, "2026-03-02T10:00:00", 150.0)
    closed_trade(client, "2026-03-03T10:00:00", 10.0)

    day = client.get("/api/analytics/day/2026-03-02").json()
    assert day["date"] == "2026-03-02"
    assert [t["id"] for t in day["trades"]] == [early["id"], late["id"]]
    assert day["pnl"] == 100.0
    assert day["review"] is None and day["checklist_complete"] is False

    items = client.get("/api/checklist-items").json()
    client.put("/api/checklist/2026-03-02", json={"checked_item_ids": [i["id"] for i in items]})
    client.put("/api/reviews/2026-03-02", json={"mood": "Calm", "lessons": "Wait for the retest."})
    day = client.get("/api/analytics/day/2026-03-02").json()
    assert day["checklist_complete"] is True
    assert day["review"]["mood"] == "Calm" and day["review"]["lessons"] == "Wait for the retest."

    empty = client.get("/api/analytics/day/2026-03-20").json()
    assert empty["trades"] == [] and empty["pnl"] == 0.0
