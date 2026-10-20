from datetime import datetime, time

from sqlalchemy import func, select

from trading_os.models import BacktestTrade, DailyReview, NewsEvent, Strategy, Trade, TradeEvent
from trading_os.services.account import get_settings, local_today
from trading_os.services.demo import seed_demo


def test_demo_journal_showcases_every_module(client):
    session_factory = client.app.state.session_factory
    with session_factory() as db:
        today = local_today(get_settings(db))
        # Mid-afternoon "now" so today's live positions and their TP1 fill are deterministic.
        created = seed_demo(db, today, trading_days=40, now=datetime.combine(today, time(15, 0)))
        assert created > 30
        assert seed_demo(db, today) == 0  # never seeds twice

        count = lambda model: db.scalar(select(func.count()).select_from(model))  # noqa: E731
        assert count(TradeEvent) > 0 and count(DailyReview) > 0 and count(NewsEvent) == 6
        open_trades = db.scalars(select(Trade).where(Trade.status == "open")).all()
        assert len(open_trades) == 2 and any(t.risk_free for t in open_trades)
        assert db.scalar(select(func.count()).select_from(Trade).where(Trade.mistakes != "[]")) > 0
        incubator = db.scalar(select(Strategy).where(Strategy.status == "incubator"))
        assert incubator is not None
        assert db.scalar(select(func.count()).where(BacktestTrade.strategy_id == incubator.id)) > 30

    positions = client.get("/api/positions").json()
    assert len(positions["positions"]) == 2
    prop = client.get("/api/risk/status").json()["prop"]
    assert prop is not None and prop["status"] == "active"
    smc = next(s for s in client.get("/api/backtest/strategies").json() if s["name"] == "SMC")
    report = client.get(f"/api/backtest/strategies/{smc['id']}/report").json()
    assert report["comparison"]["verdict"]["status"] != "insufficient"
