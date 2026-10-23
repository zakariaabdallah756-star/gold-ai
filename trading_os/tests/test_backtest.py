"""Strategy incubator & backtesting: R math, metrics, CSV import, comparison, isolation from live."""

import pytest

from trading_os.services import backtest

from .conftest import trade_payload, yesterday_iso


def test_r_from_prices_and_derive():
    assert backtest.r_from_prices("long", 100, 98, 104) == pytest.approx(2.0)
    assert backtest.r_from_prices("short", 100, 102, 103) == pytest.approx(-1.5)
    values = backtest.derive(
        {
            "direction": "long",
            "entry_price": 100,
            "stop_loss": 98,
            "take_profit": 106,
            "exit_price": 98,
            "r_multiple": 5,
        }
    )
    assert values["r_multiple"] == -1.0  # prices win over an entered R
    assert values["planned_rr"] == 3.0 and values["outcome"] == "loss"
    assert backtest.derive({"direction": "short", "r_multiple": 0})["outcome"] == "breakeven"
    with pytest.raises(backtest.BacktestError, match="result in R"):
        backtest.derive({"direction": "long", "entry_price": 100, "stop_loss": 98})
    with pytest.raises(backtest.BacktestError, match="stop loss must be below"):
        backtest.derive({"direction": "long", "entry_price": 100, "stop_loss": 101, "exit_price": 99})


def test_csv_parsing_aliases_formats_and_errors():
    text = (
        "Date;Side;Pair;Entry;SL;TP;Exit;Notes\n"
        "2026-09-01 10:00;buy;xauusd;2650;2645;2665;2665;clean\n"
        "02/09/2026;SELL;EURUSD;1,0850;1,0880;;1,0820;comma decimals\n"
        "2026.09.03 14:30;long;NAS100;18,550;18,500;;18,450;\n"
        "2026-09-04;sideways;XAUUSD;1;2;;3;\n"
        "not a date;buy;XAUUSD;1;0.5;;2;\n"
    )
    rows, errors = backtest.parse_csv(text)
    assert [r["r_multiple"] for r in rows] == [3.0, 1.0, -2.0]
    assert rows[0]["symbol"] == "XAUUSD" and rows[0]["planned_rr"] == 3.0
    assert rows[1]["trade_time"].day == 2 and rows[1]["trade_time"].month == 9  # day-first
    assert [e["line"] for e in errors] == [5, 6]
    assert "Unknown direction" in errors[0]["message"] and "Unrecognised date" in errors[1]["message"]

    r_only, _ = backtest.parse_csv("date,direction,r\n2026-09-01,long,2.5R\n2026-09-02,short,-1\n")
    assert [r["r_multiple"] for r in r_only] == [2.5, -1.0]
    with pytest.raises(backtest.BacktestError, match="direction column"):
        backtest.parse_csv("date,r\n2026-09-01,1\n")


def make_rows(results, one_r=100):
    from datetime import datetime, timedelta

    start = datetime(2026, 9, 1, 10)
    return [
        backtest.Row(
            id=i,
            symbol="XAUUSD",
            direction="long",
            pnl=r * one_r,
            r_multiple=r,
            planned_rr=2.0,
            outcome=backtest.outcome_for(r),
            entry_time=start + timedelta(days=i),
        )
        for i, r in enumerate(results)
    ]


def test_metrics_and_verdict():
    m = backtest.metrics(make_rows([2, -1, 2, -1, -1, 3]), 10_000)
    assert m["trades"] == 6 and m["win_rate"] == 50.0
    assert m["profit_factor"] == pytest.approx(7 / 3, abs=0.01)
    assert m["expectancy_r"] == pytest.approx(4 / 6, abs=0.01) and m["expectancy"] == pytest.approx(66.67, abs=0.01)
    assert m["max_drawdown_r"] == 2.0 and m["max_drawdown"] == 200.0
    assert m["avg_rr"] == pytest.approx(7 / 3, abs=0.01)  # avg win 2.33R / avg loss 1R

    bt = backtest.metrics(make_rows([2, -1] * 20), 10_000)  # 0.5R / trade, 40 trades
    assert backtest.verdict(bt, backtest.metrics(make_rows([1, -1] * 3), 10_000))["status"] == "insufficient"
    assert backtest.verdict(bt, backtest.metrics(make_rows([-1, 0.5] * 6), 10_000))["status"] == "below"
    assert backtest.verdict(bt, backtest.metrics(make_rows([2, -1] * 6), 10_000))["status"] == "in_line"
    assert backtest.verdict(bt, backtest.metrics(make_rows([3, -1] * 6), 10_000))["status"] == "above"


def test_incubator_api_flow_and_live_isolation(client):
    created = client.post("/api/backtest/strategies", json={"name": "London ORB", "description": "Opening range"})
    assert created.status_code == 201 and created.json()["status"] == "incubator"
    sid = created.json()["id"]
    assert client.post("/api/backtest/strategies", json={"name": "London ORB"}).status_code == 409

    one = client.post(
        f"/api/backtest/strategies/{sid}/trades",
        json={
            "trade_time": "2026-09-01T09:00",
            "direction": "long",
            "symbol": "xauusd",
            "entry_price": 2650,
            "stop_loss": 2645,
            "take_profit": 2665,
            "exit_price": 2660,
        },
    )
    assert one.status_code == 201 and one.json()["r_multiple"] == 2.0 and one.json()["symbol"] == "XAUUSD"
    bad = client.post(
        f"/api/backtest/strategies/{sid}/trades", json={"trade_time": "2026-09-01T09:00", "direction": "long"}
    )
    assert bad.status_code == 422

    csv_text = "date,direction,r\n2026-09-02,long,-1\n2026-09-03,short,1.5\n2026-09-04,long,oops\n"
    preview = client.post(f"/api/backtest/strategies/{sid}/import", json={"csv": csv_text, "dry_run": True}).json()
    assert preview["valid"] == 2 and preview["imported"] == 0 and preview["errors"][0]["line"] == 4
    assert len(client.get(f"/api/backtest/strategies/{sid}/trades").json()) == 1
    done = client.post(f"/api/backtest/strategies/{sid}/import", json={"csv": csv_text}).json()
    assert done["imported"] == 2

    report = client.get(f"/api/backtest/strategies/{sid}/report").json()
    assert report["metrics"]["trades"] == 3 and report["metrics"]["net_r"] == 2.5
    assert report["risk_per_trade"] == 100.0  # 1% of the 10k starting balance
    assert report["metrics"]["net_pnl"] == 250.0
    assert report["equity_curve"][-1]["r"] == 2.5
    assert report["comparison"]["verdict"]["status"] == "insufficient"
    rows = {row["key"]: row for row in report["comparison"]["rows"]}
    assert rows["max_drawdown_pct"]["live"] is None and rows["max_drawdown_pct"]["delta"] is None
    assert rows["trades"]["live"] == 0

    client.put(f"/api/backtest/strategies/{sid}/settings", json={"backtest_risk_per_trade": 50})
    assert client.get(f"/api/backtest/strategies/{sid}/report").json()["metrics"]["net_pnl"] == 125.0

    # Backtests never touch the live journal, equity or analytics.
    assert client.get("/api/analytics").json()["summary"]["trades"] == 0
    assert client.get("/api/risk/status").json()["current_equity"] == 10_000

    # Live trades of the same strategy appear on the live side of the comparison.
    client.post(
        "/api/trades",
        json=trade_payload(strategy="London ORB", status="closed", entry_time=yesterday_iso(), exit_price=2660.0),
    )
    live = client.get(f"/api/backtest/strategies/{sid}/report").json()["live_metrics"]
    assert live["trades"] == 1

    assert client.put(f"/api/backtest/strategies/{sid}/status", json={"status": "live"}).json()["status"] == "live"
    listed = {s["name"]: s for s in client.get("/api/backtest/strategies").json()}
    assert listed["London ORB"]["trades"] == 3 and listed["London ORB"]["status"] == "live"

    exported = client.get(f"/api/backtest/strategies/{sid}/export.csv")
    assert exported.headers["content-disposition"].endswith('"backtest-london-orb.csv"')
    assert exported.text.count("\n") == 4
    assert client.delete(f"/api/backtest/strategies/{sid}/trades").status_code == 204
    assert client.get(f"/api/backtest/strategies/{sid}/trades").json() == []
