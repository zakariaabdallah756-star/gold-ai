from datetime import datetime, time

from sqlalchemy import func, select

from trading_os.models import (
    AlertRule,
    BacktestTrade,
    DailyReview,
    NewsEvent,
    Strategy,
    StrategyAlert,
    Trade,
    TradeEvent,
)
from trading_os.services.account import get_settings, local_today
from trading_os.services.demo import seed_demo
from trading_os.services.signal_parser import parse_signal


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

        # Alert history comes from the real engine on synthetic candles (how many depends on the date);
        # alerts never create trades.
        rule = db.scalar(select(AlertRule))
        assert rule is not None and rule.provider == "demo" and rule.strategy_id is not None
        assert db.scalar(select(func.count()).select_from(Trade)) == created
        for alert in db.scalars(select(StrategyAlert)):
            parsed = parse_signal(alert.signal_text, [alert.symbol])
            assert parsed.symbol == alert.symbol and parsed.stop_loss == alert.stop_loss

    positions = client.get("/api/positions").json()
    assert len(positions["positions"]) == 2
    prop = client.get("/api/risk/status").json()["prop"]
    assert prop is not None and prop["status"] == "active"
    smc = next(s for s in client.get("/api/backtest/strategies").json() if s["name"] == "SMC")
    report = client.get(f"/api/backtest/strategies/{smc['id']}/report").json()
    assert report["comparison"]["verdict"]["status"] != "insufficient"
