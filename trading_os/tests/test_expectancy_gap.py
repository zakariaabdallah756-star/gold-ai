"""Backtest vs live: the expectancy gap, risk-neutral drawdown rows and strategy renames."""

import pytest

from trading_os.services import backtest

from .conftest import trade_payload, yesterday_iso


def side(trades, expectancy_r, expectancy=None, **extra):
    return {"trades": trades, "expectancy_r": expectancy_r, "expectancy": expectancy, **extra}


def test_expectancy_gap_in_r_money_and_edge_kept():
    gap = backtest.expectancy_gap(side(40, 0.5), side(12, 0.2, 15.0), 100.0)
    assert gap["gap_r"] == -0.3 and gap["gap_pct"] == -60.0 and gap["edge_retained_pct"] == 40.0
    assert (gap["backtest_money"], gap["live_money_same_basis"], gap["gap_money_same_basis"]) == (50.0, 20.0, -30.0)
    assert gap["live_money_actual"] == 15.0  # real size and fees, shown next to the same-basis figure

    flat = backtest.expectancy_gap(side(40, 0.0), side(12, 0.2), 100.0)
    assert flat["gap_pct"] is None and flat["edge_retained_pct"] is None
    losing = backtest.expectancy_gap(side(40, -0.2), side(12, 0.1), None)
    assert losing["gap_pct"] == 150.0 and losing["edge_retained_pct"] is None and losing["gap_money_same_basis"] is None
    assert backtest.expectancy_gap(side(40, 0.5), side(0, None), 100.0) is None


def test_report_shows_the_gap_and_keeps_live_history_after_a_rename(client):
    sid = client.post("/api/backtest/strategies", json={"name": "London ORB"}).json()["id"]
    csv_text = "date,direction,r\n2026-09-02,long,2\n2026-09-03,short,-1\n2026-09-04,long,1.5\n"
    client.post(f"/api/backtest/strategies/{sid}/import", json={"csv": csv_text})
    payload = trade_payload(strategy="London ORB", status="closed", entry_time=yesterday_iso(), exit_price=2660.0)
    assert client.post("/api/trades", json=payload).status_code == 201  # +2R live

    report = client.get(f"/api/backtest/strategies/{sid}/report").json()
    gap = report["comparison"]["gap"]
    assert gap["backtest_r"] == 0.83 and gap["live_r"] == 2.0 and gap["gap_r"] == 1.17
    assert gap["gap_money_same_basis"] == 117.0 and gap["live_money_actual"] == 200.0
    rows = {row["key"]: row for row in report["comparison"]["rows"]}
    assert rows["expectancy_same_basis"]["backtest"] == 83.0 and rows["expectancy_same_basis"]["unit"] == "money"
    assert rows["max_drawdown_r"]["backtest"] == 1.0 and rows["max_drawdown_r"]["unit"] == "r"
    assert rows["max_drawdown"]["unit"] == "money" and rows["win_rate"]["unit"] == "pct"
    assert rows["profit_factor"]["infinite"] == {"backtest": False, "live": True}
    assert report["live_names"] == ["London ORB"]

    strategy = next(s for s in client.get("/api/strategies").json() if s["id"] == sid)
    renamed = client.put(f"/api/strategies/{sid}", json={**strategy, "name": "London ORB v2"})
    assert renamed.status_code == 200 and renamed.json()["former_names"] == ["London ORB"]
    report = client.get(f"/api/backtest/strategies/{sid}/report").json()
    assert report["live_metrics"]["trades"] == 1 and report["live_names"] == ["London ORB v2", "London ORB"]
    listed = next(s for s in client.get("/api/backtest/strategies").json() if s["id"] == sid)
    assert listed["live_trades"] == 1

    # A new strategy that takes the old name owns those journal trades from now on.
    body = {k: v for k, v in strategy.items() if k not in ("id", "former_names")}
    other = client.post("/api/strategies", json={**body, "name": "London ORB"})
    assert other.status_code == 201
    report = client.get(f"/api/backtest/strategies/{sid}/report").json()
    assert report["live_names"] == ["London ORB v2"] and report["live_metrics"]["trades"] == 0
    assert client.delete(f"/api/strategies/{other.json()['id']}").status_code == 204
    assert client.get(f"/api/backtest/strategies/{sid}/report").json()["live_metrics"]["trades"] == 1

    # Renaming back drops the old name from the list of former names.
    current = next(s for s in client.get("/api/strategies").json() if s["id"] == sid)
    back = client.put(f"/api/strategies/{sid}", json={**current, "name": "London ORB"}).json()
    assert back["former_names"] == ["London ORB v2"]


@pytest.mark.parametrize("with_live", [False, True])
def test_backtest_pdf_renders_the_gap(client, with_live):
    sid = client.post("/api/backtest/strategies", json={"name": "Asia range"}).json()["id"]
    client.post(f"/api/backtest/strategies/{sid}/import", json={"csv": "date,direction,r\n2026-09-02,long,2\n"})
    if with_live:
        client.post(
            "/api/trades",
            json=trade_payload(strategy="Asia range", status="closed", entry_time=yesterday_iso(), exit_price=2640.0),
        )
    response = client.get(f"/api/reports/backtest/{sid}.pdf")
    assert response.status_code == 200 and response.content.startswith(b"%PDF")
