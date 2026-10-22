"""Partial fills booked on the day they were filled: calendar, day detail, reviews, dashboard and PDF.

Bookings follow the position ledger, so the calendar day always equals the status bar's realised
P&L of that day and the prop tracker's daily P&L - for open and closed trades alike.
"""

from datetime import date, datetime, time, timedelta

import pytest

from trading_os.services import i18n, prop, report_pdf

from .conftest import complete_checklist, trade_payload, update_settings, yesterday_iso


@pytest.fixture
def live(client):
    complete_checklist(client)
    update_settings(client, warn_outside_sessions=False)
    return client


def create(client, **overrides) -> dict:
    response = client.post("/api/trades", json=trade_payload(**overrides))
    assert response.status_code == 201, response.text
    return response.json()["trade"]


def post(client, path: str, body: dict, status: int = 200) -> dict:
    response = client.post(path, json=body)
    assert response.status_code == status, response.text
    return response.json()


def _calendar_day(client, day: str) -> dict:
    month = client.get("/api/analytics/calendar", params={"month": day[:7]}).json()
    return next(d for d in month["days"] if d["date"] == day)


def _prop_daily(client, day: str) -> float:
    start = datetime.combine(date.fromisoformat(day), time.min)
    with client.app.state.session_factory() as db:
        return prop.daily_realized(db, start, start + timedelta(days=1)).get(date.fromisoformat(day), 0.0)


def test_partial_fills_are_booked_on_their_fill_day_everywhere(live):
    entry = yesterday_iso()  # yesterday 12:00
    yesterday, today = entry[:10], live.get("/api/plan").json()["today"]
    trade = create(live, entry_time=entry, position_size=0.3, fees=4.0)
    post(live, f"/api/trades/{trade['id']}/partial-close", {"lots": 0.1, "price": 2660, "time": f"{yesterday}T13:00"})

    # Still open: the +100 partial is realised P&L of yesterday - calendar, day detail, review, dashboard.
    day = _calendar_day(live, yesterday)
    assert (day["pnl"], day["trades"], day["partials"]) == (100.0, 0, 1)
    detail = live.get(f"/api/analytics/day/{yesterday}").json()
    assert detail["pnl"] == 100.0 and [t["id"] for t in detail["trades"]] == [trade["id"]]
    assert detail["stats"] == {"trades": 0, "wins": 0, "losses": 0, "win_rate": None, "partials": 1}
    (fill,) = detail["fills"]
    assert (fill["kind"], fill["lots"], fill["price"], fill["pnl"]) == ("partial", 0.1, 2660.0, 100.0)
    assert fill["closes_trade"] is False and fill["trade_status"] == "open" and fill["trade_pnl"] is None
    assert fill["time"] == f"{yesterday}T13:00:00" and fill["r_multiple"] is None
    stats = live.get(f"/api/reviews/{yesterday}").json()["stats"]
    assert (stats["net_pnl"], stats["trades"], stats["partials"]) == (100.0, 0, 1)
    report = live.get("/api/analytics").json()
    assert report["summary"]["trades"] == 0 and report["summary"]["realized_pnl"] == 100.0
    assert [(d["date"], d["pnl"], d["partials"]) for d in report["daily_pnl"]] == [(yesterday, 100.0, 1)]
    assert report["summary"]["end_equity"] == live.get("/api/risk/status").json()["current_equity"] == 10_100.0

    # Closed today at a loss: -200 on the fill, the 4 of fees with it; the trade itself lost 104.
    closed = post(live, f"/api/trades/{trade['id']}/close", {"price": 2640})["trade"]
    assert closed["pnl"] == -104.0
    day, now = _calendar_day(live, yesterday), _calendar_day(live, today)
    assert (day["pnl"], day["trades"], day["partials"]) == (100.0, 0, 1)
    assert (now["pnl"], now["trades"], now["losses"], now["partials"]) == (-204.0, 1, 1, 0)
    status = live.get("/api/risk/status").json()
    assert now["pnl"] == status["realized_pnl_today"] == _prop_daily(live, today) == -204.0
    assert day["pnl"] == _prop_daily(live, yesterday)

    detail = live.get(f"/api/analytics/day/{today}").json()
    assert detail["pnl"] == -204.0 and detail["stats"]["trades"] == 1 and detail["stats"]["losses"] == 1
    (fill,) = detail["fills"]
    assert (fill["kind"], fill["pnl"], fill["closes_trade"], fill["trade_pnl"]) == ("close", -204.0, True, -104.0)
    assert fill["r_multiple"] == closed["r_multiple"] and fill["trade_exit_time"] == closed["exit_time"]
    assert live.get(f"/api/analytics/day/{yesterday}").json()["fills"][0]["trade_status"] == "closed"
    assert live.get(f"/api/reviews/{today}").json()["stats"]["net_pnl"] == -204.0
    history = {row["day"]: row for row in live.get("/api/reviews", params={"days": 2}).json()}
    assert (history[yesterday]["pnl"], history[yesterday]["trades"], history[yesterday]["partials"]) == (100.0, 0, 1)
    assert (history[today]["pnl"], history[today]["trades"], history[today]["partials"]) == (-204.0, 1, 0)

    report = live.get("/api/analytics").json()
    assert [p["equity"] for p in report["equity_curve"]] == [10_000.0, 10_100.0, 9_896.0]
    assert report["summary"]["net_pnl"] == report["summary"]["realized_pnl"] == -104.0
    assert report["summary"]["max_drawdown"] == 204.0
    ranged = live.get("/api/analytics", params={"date_from": today}).json()
    assert ranged["summary"]["start_equity"] == 10_100.0 and ranged["summary"]["end_equity"] == 9_896.0
    summary = ranged["summary"]
    assert (summary["trades"], summary["net_pnl"], summary["realized_pnl"]) == (1, -104.0, -204.0)
    assert [(d["date"], d["pnl"]) for d in ranged["daily_pnl"]] == [(today, -204.0)]

    # Undoing the close takes today's booking away; the partial stays on yesterday.
    undone = live.delete(f"/api/trades/{trade['id']}/events/{closed['events'][-1]['id']}")
    assert undone.status_code == 200, undone.text
    now = _calendar_day(live, today)
    assert (now["pnl"], now["trades"], now["partials"]) == (0.0, 0, 0)
    assert _calendar_day(live, yesterday)["pnl"] == 100.0
    assert live.get(f"/api/analytics/day/{today}").json()["fills"] == []


def test_partial_of_an_open_trade_shows_today_like_the_status_bar(live):
    trade = create(live, position_size=0.3, fees=2.0)
    post(live, f"/api/trades/{trade['id']}/partial-close", {"lots": 0.1, "price": 2655})  # +50 now
    today = live.get("/api/plan").json()["today"]
    day = _calendar_day(live, today)
    assert (day["pnl"], day["trades"], day["partials"]) == (50.0, 0, 1)
    assert day["pnl"] == live.get("/api/risk/status").json()["realized_pnl_today"] == _prop_daily(live, today)
    month = live.get("/api/analytics/calendar").json()["month_total"]
    assert (month["trading_days"], month["green_days"], month["best_day"]["pnl"]) == (1, 1, 50.0)
    assert live.get(f"/api/reviews/{today}").json()["stats"]["partials"] == 1


def test_booked_reports_load_fills_without_a_query_per_trade(live):
    from sqlalchemy import event

    def statements(path: str) -> int:
        seen = []
        engine = live.app.state.engine

        def count(*_args) -> None:
            seen.append(1)

        event.listen(engine, "before_cursor_execute", count)
        try:
            assert live.get(path).status_code == 200
        finally:
            event.remove(engine, "before_cursor_execute", count)
        return len(seen)

    def scale_out() -> None:
        trade = create(live, position_size=0.3)
        post(live, f"/api/trades/{trade['id']}/partial-close", {"lots": 0.1, "price": 2655})
        post(live, f"/api/trades/{trade['id']}/close", {"price": 2652})

    scale_out()
    today = live.get("/api/plan").json()["today"]
    paths = ["/api/analytics", "/api/analytics/calendar", f"/api/analytics/day/{today}", f"/api/reviews/{today}"]
    before = [statements(path) for path in paths]
    for _ in range(4):
        scale_out()
    assert [statements(path) for path in paths] == before


# ------------------------------------------------------------------ PDF


def _pdf_text(content: bytes) -> str:
    pypdf = pytest.importorskip("pypdf")
    import io

    return " ".join(" ".join(page.extract_text().split()) for page in pypdf.PdfReader(io.BytesIO(content)).pages)


def _report(**summary) -> dict:
    base = {"trades": 0, "net_pnl": None, "realized_pnl": 50.0}
    return {
        "summary": {**base, **summary},
        "equity_curve": [{"equity": 10_000.0}, {"equity": 10_050.0}],
        "daily_pnl": [
            {"date": "2026-03-02", "pnl": 50.0, "trades": 0, "wins": 0, "losses": 0, "partials": 1, "cumulative": 50.0}
        ],
    }


def test_pdf_of_a_period_with_only_partial_exits_shows_what_was_realised():
    text = _pdf_text(report_pdf.build_performance_pdf(_report(), account="Main", currency="USD", period="March"))
    assert "No closed trades in this period." in text
    assert "Realised in this period: +50.00 USD" in text
    assert "Realised equity (USD)" in text and "Partial exits" in text
    assert "each partial exit on the day it was filled" in text

    with i18n.using("it"):
        text = _pdf_text(report_pdf.build_performance_pdf(_report(), account="Main", currency="USD", period="Marzo"))
    assert "Realizzato nel periodo: +50.00 USD" in text and "Equity realizzata (USD)" in text
    assert "Uscite parziali" in text


def test_performance_pdf_explains_partials_booked_outside_the_closed_trades(live):
    trade = create(live, entry_time=yesterday_iso(), position_size=0.3, fees=0.0)
    yesterday = yesterday_iso()[:10]
    post(live, f"/api/trades/{trade['id']}/partial-close", {"lots": 0.1, "price": 2660, "time": f"{yesterday}T13:00"})
    post(live, f"/api/trades/{trade['id']}/close", {"price": 2655})  # +100 yesterday, +100 today
    today = live.get("/api/plan").json()["today"]

    response = live.get("/api/reports/performance.pdf", params={"date_from": today})
    assert response.status_code == 200
    text = _pdf_text(response.content)
    assert "Realised in this period: +100.00 USD" in text  # the trade made 200, half of it booked yesterday
    assert "Realised equity (USD)" in text and "Partial exits" in text

    everything = _pdf_text(live.get("/api/reports/performance.pdf").content)
    assert "Realised in this period" not in everything  # all-time: booked == closed trades' P&L
