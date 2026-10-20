"""Synthetic demo journal used by ``--demo`` (written to a separate data dir).

Trades are generated through the regular trade service so every derived
field and guardrail annotation is produced exactly as for real entries.
Strategies and emotional states are given different edges on purpose, so
the breakdown tables have something meaningful to show.

Recent trades are partly "managed" (scale-out targets, break-even after TP1)
and replayed through the position engine, today has two live positions, and
the journal comes with mistakes, post-market reviews, upcoming news and two
backtested strategies (one live for the backtest-vs-live comparison, one in
the incubator).
"""

from __future__ import annotations

import random
from datetime import date, datetime, time, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..constants import DEFAULT_TP_SPLITS
from ..models import (
    AccountSettings,
    AlertRule,
    BacktestTrade,
    ChecklistDay,
    ChecklistItem,
    Confluence,
    DailyReview,
    Market,
    NewsEvent,
    PropChallenge,
    Strategy,
    Trade,
)
from ..schemas import TargetIn, TradeIn
from . import alerts, market_data, positions, prop
from .account import local_now
from .backtest import derive
from .sizing import round_lots
from .trades import create_trade

# symbol: (weight, start price, typical stop range in pips/points, fee per lot)
INSTRUMENTS = {
    "XAUUSD": (45, 2350.0, (25, 70), 7.0),
    "EURUSD": (14, 1.0850, (8, 22), 7.0),
    "GBPUSD": (10, 1.2700, (10, 28), 7.0),
    "USDJPY": (6, 151.20, (12, 30), 7.0),
    "NAS100": (15, 18_500.0, (30, 80), 0.0),
    "US30": (10, 39_000.0, (50, 140), 0.0),
}

# strategy: (win probability, (min, max) planned R:R)
STRATEGIES = {
    "SMC": (0.47, (2.0, 3.5)),
    "Price Action": (0.45, (2.0, 3.0)),
    "Breakout": (0.36, (1.6, 2.6)),
}

# emotion: (weight, win-probability adjustment, chance of breaking rules)
EMOTIONS = {
    "Calm": (30, 0.06, 0.05),
    "Confident": (20, 0.04, 0.08),
    "Focused": (15, 0.05, 0.05),
    "Anxious": (10, -0.08, 0.30),
    "FOMO": (9, -0.15, 0.70),
    "Impatient": (7, -0.10, 0.50),
    "Revenge": (5, -0.20, 0.90),
    "Tired": (4, -0.06, 0.25),
}

# Mistakes a rule-breaking trade is tagged with, by emotional state.
EMOTION_MISTAKES = {
    "FOMO": ("FOMO entry", "Late entry"),
    "Revenge": ("Revenge trade", "Oversized"),
    "Impatient": ("Late entry", "Ignored plan"),
    "Anxious": ("Early exit", "Moved stop loss"),
    "Tired": ("Ignored plan", "Overtrading"),
}
DEFAULT_MISTAKES = ("Ignored plan", "Early exit")

SESSION_HOURS_UTC = {"London": (7, 11), "New York": (12, 16), "Asia": (0, 5)}

NOTES = {
    "win": [
        "Clean execution, waited for confirmation.",
        "Followed the plan; let it run to target.",
        "Entry at the level, textbook reaction.",
    ],
    "loss": [
        "Valid setup, just didn't work. Loss within plan.",
        "Entered early before confirmation.",
        "News spike took out the stop.",
        "Moved stop against the plan - mistake.",
    ],
    "breakeven": ["Moved to breakeven after 1R, stopped out flat."],
}

REVIEW_TEXT = {
    "good": {
        "went_well": [
            "Waited for every confirmation and sized correctly.",
            "Skipped two B-setups, only took the A+ one.",
            "Managed the partials exactly as planned.",
        ],
        "lessons": [
            "Patience pays - the best trade came after an hour of waiting.",
            "Scaling out at TP1 keeps me calm for the runner.",
        ],
        "tomorrow_focus": ["Same process: levels first, trigger second.", "Only trade the London open."],
    },
    "bad": {
        "went_wrong": [
            "Chased a move after missing the entry.",
            "Took a revenge trade after the first loss.",
            "Traded through the news window.",
        ],
        "lessons": [
            "After a loss, step away for 30 minutes.",
            "If I miss the entry, the trade is gone - no chasing.",
        ],
        "tomorrow_focus": ["Max two trades, walk away after a loss.", "Mark the news times before the open."],
    },
}

# Pure-R backtest generators: (win probability, (min, max) R:R, trades)
BACKTESTS = {
    "SMC": (0.49, (2.0, 3.4), 120),
    "London Breakout v2": (0.43, (1.8, 2.8), 64),
}
INCUBATOR_STRATEGY = (
    "London Breakout v2",
    "Asia-range breakout at the London open, entry on the first retest.",
    "M15, M5",
    "Mark the Asia high/low. Trade the first M15 close outside the range during London, "
    "enter on the retest, stop at the range midpoint. Skip days with high-impact news before 10:00.",
)

UPCOMING_NEWS = (  # (days ahead, local time, title, currency, impact)
    (1, time(14, 30), "Unemployment Claims", "USD", "medium"),
    (2, time(14, 30), "CPI (Inflation)", "USD", "high"),
    (3, time(14, 15), "ECB Rate Decision", "EUR", "high"),
    (4, time(14, 30), "Non-Farm Payrolls (NFP)", "USD", "high"),
    (6, time(20, 0), "FOMC Rate Decision", "USD", "high"),
    (8, time(13, 0), "BoE Rate Decision", "GBP", "high"),
)


def _weighted(rng: random.Random, table: dict) -> str:
    names = list(table)
    return rng.choices(names, weights=[table[name][0] for name in names])[0]


def _targets(rng: random.Random, entry: float, risk: float, sign: int, rr: float, min_rr: float, digits: int):
    """Scale-out targets in R whose blended R:R still meets the account minimum."""
    count = rng.choice([2, 3])
    levels = [1.0, rr] if count == 2 else [1.0, (1.0 + rr) / 2, rr]
    splits = DEFAULT_TP_SPLITS[count]
    blended = sum(level * pct for level, pct in zip(levels, splits, strict=True)) / 100
    if blended < min_rr * 1.05:
        levels = [level * min_rr * 1.05 / blended for level in levels]
    return [
        TargetIn(price=round(entry + sign * risk * level, digits), close_pct=pct)
        for level, pct in zip(levels, splits, strict=True)
    ]


def _replay(db: Session, settings: AccountSettings, trade: Trade, rng: random.Random, win_probability: float):
    """Drive a managed trade through the position engine: stop, or TP1 + break-even and beyond."""
    when = trade.entry_time + timedelta(minutes=rng.randint(10, 90))
    if rng.random() > win_probability:
        positions.price_update(db, settings, trade, price=trade.stop_loss, when=when)
        return
    for level, target in enumerate(sorted(trade.targets, key=lambda t: t.level), start=1):
        when += timedelta(minutes=rng.randint(10, 90) if level > 1 else 0)
        if level > 1 and rng.random() < 0.4:  # gave back the move: the break-even stop closes the rest
            positions.price_update(db, settings, trade, price=trade.current_stop, when=when)
            return
        positions.price_update(db, settings, trade, price=target.price, when=when)
        if trade.status == "closed":
            return


def _trade_in(**fields) -> TradeIn:
    return TradeIn(tags=["demo"] + fields.pop("extra_tags", []), **fields)


def seed_demo(db: Session, today: date, trading_days: int = 80, seed: int = 11, now: datetime | None = None) -> int:
    """Populate an empty journal with demo data. Returns trades created."""
    if db.scalar(select(Trade.id).limit(1)) is not None:
        return 0

    rng = random.Random(seed)
    settings = db.get(AccountSettings, 1)
    now = now or local_now(settings)
    markets = {market.symbol: market for market in db.scalars(select(Market))}
    confluences = [c.name for c in db.scalars(select(Confluence))]
    checklist_ids = [item.id for item in db.scalars(select(ChecklistItem))]
    prices = {symbol: spec[1] for symbol, spec in INSTRUMENTS.items()}
    equity = settings.starting_balance

    days = []
    cursor = today - timedelta(days=1)
    while len(days) < trading_days:
        if cursor.weekday() < 5:
            days.append(cursor)
        cursor -= timedelta(days=1)
    days.reverse()
    managed_from = days[-25]
    days.append(today)

    day_log: dict[date, dict] = {}
    created = 0
    for day in days:
        db.add(
            ChecklistDay(
                day=day,
                checked_item_ids=checklist_ids if rng.random() < 0.9 or day == today else checklist_ids[:3],
                bias=rng.choice(["bullish", "bearish", "neutral"]),
                key_levels="PDH / PDL, weekly open, Asia range high/low",
                news=rng.choice(["", "USD CPI 14:30", "FOMC minutes 20:00", "NFP 14:30", "ECB speech"]),
                completed_at=datetime.combine(day, time(7, 30)),
            )
        )
        db.commit()

        is_today = day == today
        trades_today = 2 if is_today else rng.choices([0, 1, 2, 3], weights=[15, 45, 30, 10])[0]
        log = day_log.setdefault(day, {"pnl": 0.0, "trades": 0, "broke": 0, "mistakes": []})
        for index in range(trades_today):
            symbol = "XAUUSD" if is_today and index == 0 else _weighted(rng, INSTRUMENTS)
            market = markets[symbol]
            _, _, stop_range, fee_per_lot = INSTRUMENTS[symbol]
            prices[symbol] *= 1 + rng.gauss(0, 0.006)
            entry = round(prices[symbol], 5 if market.pip_size < 0.01 else 2)

            strategy = rng.choice(list(STRATEGIES))
            base_wr, rr_range = STRATEGIES[strategy]
            emotion = rng.choice(["Calm", "Focused"]) if is_today else _weighted(rng, EMOTIONS)
            _, wr_adjust, break_chance = EMOTIONS[emotion]
            # Today's trades are live open positions, so they must pass the guardrails.
            broke_rules = rng.random() < break_chance and not is_today

            session = rng.choices(["London", "New York", "Asia"], weights=[50, 42, 8])[0]
            start_hour, end_hour = SESSION_HOURS_UTC[session]
            entry_time = datetime.combine(day, time(rng.randint(start_hour, end_hour), rng.choice([0, 15, 30, 45])))
            if is_today:  # opened in the last couple of hours (never before midnight or in the future)
                entry_time = max(datetime.combine(day, time.min), now - timedelta(minutes=110 - 70 * index))

            direction = rng.choice(["long", "short"])
            sign = 1 if direction == "long" else -1
            stop_pips = rng.uniform(*stop_range)
            stop = entry - sign * stop_pips * market.pip_size
            rr = rng.uniform(1.1, 1.8) if broke_rules and rng.random() < 0.5 else rng.uniform(*rr_range)
            if is_today:
                rr = max(rr, settings.min_rr)
            take_profit = entry + sign * stop_pips * rr * market.pip_size

            pip_value = market.contract_size * market.pip_size * (1.0 / entry if symbol == "USDJPY" else 1.0)
            lots = round_lots(equity * 0.01 / (stop_pips * pip_value), market) or market.min_lot

            win_probability = base_wr + wr_adjust - (0.08 if broke_rules else 0.0)
            digits = 5 if market.pip_size < 0.01 else 3 if market.pip_size < 1 else 1
            managed = is_today or (day >= managed_from and not broke_rules and rr >= 1.6 and rng.random() < 0.45)
            picked = rng.sample(confluences, k=rng.randint(1, 2) if broke_rules else rng.randint(3, 4))
            mistakes = []
            if broke_rules:
                options = EMOTION_MISTAKES.get(emotion, DEFAULT_MISTAKES)
                mistakes = list(options[: rng.randint(1, len(options))])
            common = dict(
                entry_time=entry_time,
                symbol=symbol,
                direction=direction,
                entry_price=round(entry, digits),
                stop_loss=round(stop, digits),
                position_size=lots,
                fees=round(fee_per_lot * lots, 2),
                strategy=strategy,
                timeframe=rng.choice(["M5", "M15", "H1"]),
                session=session,
                confluences=picked,
                emotion=emotion,
                mistakes=mistakes,
                rule_compliant=False if broke_rules else None,
                extra_tags=["a-plus-setup"] if rng.random() < 0.2 else [],
            )

            if managed:
                targets = _targets(rng, entry, stop_pips * market.pip_size, sign, rr, settings.min_rr, digits)
                trail = is_today and index == 1
                trade, _ = create_trade(
                    db,
                    settings,
                    _trade_in(
                        status="open",
                        targets=targets,
                        be_enabled=True,
                        trail_enabled=trail,
                        trail_distance_pips=round(stop_pips * 0.8, 1) if trail else None,
                        notes="Scaled out at the targets; stop to break-even after TP1.",
                        **common,
                    ),
                )
                if is_today:
                    first_fill = trade.entry_time + timedelta(minutes=35)
                    if index == 0 and first_fill <= now:  # TP1 already paid: the position is risk-free
                        positions.price_update(db, settings, trade, price=targets[0].price, when=first_fill)
                else:
                    _replay(db, settings, trade, rng, win_probability)
            else:
                roll = rng.random()
                if roll < win_probability:
                    realised_r = rr if rng.random() < 0.7 else rng.uniform(0.8, rr)
                elif roll < win_probability + 0.07:
                    realised_r = 0.0
                else:
                    realised_r = -1.0 if rng.random() < 0.8 else -rng.uniform(0.4, 0.9)
                exit_price = entry + sign * stop_pips * realised_r * market.pip_size
                outcome = "win" if realised_r > 0 else "loss" if realised_r < 0 else "breakeven"
                trade, _ = create_trade(
                    db,
                    settings,
                    _trade_in(
                        status="closed",
                        exit_time=entry_time + timedelta(minutes=rng.randint(15, 300)),
                        take_profit=round(take_profit, digits),
                        exit_price=round(exit_price, digits),
                        notes=rng.choice(NOTES[outcome]),
                        **common,
                    ),
                )
            if trade.pnl is not None:
                equity += trade.pnl
                log["pnl"] += trade.pnl
            log["trades"] += 1
            log["broke"] += int(broke_rules)
            log["mistakes"] += [m for m in mistakes if m not in log["mistakes"]]
            created += 1

    _seed_reviews(db, rng, days[-30:-1], day_log)
    _seed_news(db, today)
    _seed_backtests(db, rng, days[0])
    db.commit()
    _seed_prop_challenge(db, settings, today)
    _seed_alerts(db, settings, now)
    return created


def _seed_reviews(db: Session, rng: random.Random, days: list[date], day_log: dict[date, dict]) -> None:
    for day in days:
        log = day_log.get(day)
        if not log or not log["trades"] or rng.random() > 0.75:
            continue
        good = log["broke"] == 0
        text = REVIEW_TEXT["good" if good else "bad"]
        if not good:
            mood = rng.choice(["Frustrated", "Tilted", "Anxious"])
        else:
            mood = rng.choice(["Calm", "Satisfied"] if log["pnl"] >= 0 else ["Neutral", "Calm"])
        db.add(
            DailyReview(
                day=day,
                discipline_rating=rng.choice([4, 5]) if good else rng.choice([1, 2, 3]),
                mood=mood,
                followed_plan=good,
                went_well=rng.choice(text.get("went_well", ["Stopped after the second loss."])),
                went_wrong="" if good else rng.choice(text["went_wrong"]),
                lessons=rng.choice(text["lessons"]),
                tomorrow_focus=rng.choice(text["tomorrow_focus"]),
                mistakes=list(log["mistakes"]),
            )
        )


def _seed_news(db: Session, today: date) -> None:
    for days_ahead, at, title, currency, impact in UPCOMING_NEWS:
        day = today + timedelta(days=days_ahead)
        while day.weekday() >= 5:
            day += timedelta(days=1)
        db.add(NewsEvent(time=datetime.combine(day, at), title=title, currency=currency, impact=impact))


def _seed_backtests(db: Session, rng: random.Random, journal_start: date) -> None:
    name, description, timeframes, rules = INCUBATOR_STRATEGY
    if db.scalar(select(Strategy).where(Strategy.name == name)) is None:
        db.add(
            Strategy(
                name=name,
                description=description,
                timeframes=timeframes,
                rules=rules,
                min_confluences=2,
                status="incubator",
                allowed_sessions=["London"],
            )
        )
        db.flush()
    strategies = {s.name: s for s in db.scalars(select(Strategy).where(Strategy.name.in_(list(BACKTESTS))))}
    for strategy_name, (win_probability, rr_range, count) in BACKTESTS.items():
        strategy = strategies.get(strategy_name)
        if strategy is None:
            continue
        price = INSTRUMENTS["XAUUSD"][1] * 0.93
        start = datetime.combine(journal_start - timedelta(days=270), time(8, 0))
        for index in range(count):
            when = start + timedelta(
                days=index * 260 // count, hours=rng.randint(0, 6), minutes=rng.choice([0, 15, 30])
            )
            price *= 1 + rng.gauss(0, 0.005)
            direction = rng.choice(["long", "short"])
            sign = 1 if direction == "long" else -1
            risk = rng.uniform(3.0, 7.0)
            rr = rng.uniform(*rr_range)
            roll = rng.random()
            result = rr if roll < win_probability else 0.0 if roll < win_probability + 0.06 else -1.0
            entry = round(price, 2)
            values = derive(
                {
                    "direction": direction,
                    "entry_price": entry,
                    "stop_loss": round(entry - sign * risk, 2),
                    "take_profit": round(entry + sign * risk * rr, 2),
                    "exit_price": round(entry + sign * risk * result, 2),
                }
            )
            db.add(
                BacktestTrade(
                    strategy_id=strategy.id,
                    trade_time=when,
                    symbol="XAUUSD",
                    session="London" if when.hour < 12 else "New York",
                    timeframe="M15",
                    tags=["demo-backtest"],
                    **values,
                )
            )


def _seed_prop_challenge(db: Session, settings: AccountSettings, today: date) -> None:
    """An active FTMO-style challenge whose start date shows a running challenge with some progress."""
    preset = next(p for p in prop.PRESETS if p["key"] == "ftmo_challenge")
    rules = {
        k: preset[k]
        for k in (
            "firm",
            "phase",
            "profit_target_pct",
            "max_daily_loss_pct",
            "max_total_drawdown_pct",
            "drawdown_mode",
            "min_trading_days",
            "max_calendar_days",
        )
    }
    best: tuple[float, date] | None = None
    for days_back in range(5, 61):
        start = today - timedelta(days=days_back)
        candidate = PropChallenge(name="", account_size=10_000.0, start_date=start, preset="ftmo_challenge", **rules)
        metrics = prop.compute(db, settings, candidate, today)
        progress = metrics.progress_pct or 0.0
        if metrics.evaluation.status == "active" and 0 < progress < 100 and (best is None or progress > best[0]):
            best = (progress, start)
    start = best[1] if best else today - timedelta(days=10)
    challenge = PropChallenge(
        name="FTMO 10k Challenge",
        account_size=10_000.0,
        start_date=start,
        preset="ftmo_challenge",
        notes="Demo challenge - verify the current rules on the firm's website.",
        **rules,
    )
    db.add(challenge)
    db.commit()
    prop.activate(db, challenge)
    prop.refresh(db, settings, challenge)


DEMO_ALERT_SYMBOLS = ("XAUUSD", "EURUSD", "NAS100")


def _seed_alerts(db: Session, settings: AccountSettings, now: datetime) -> None:
    """A strategy-linked alert rule on synthetic demo candles, with the setups the engine found in the last two days.

    The history is produced by the real engine (``alerts.evaluate`` on each past closed candle), so the
    feed shows what the scanner would have sent. The scanner itself stays off until the user turns it on.
    """
    strategy = db.scalar(select(Strategy).where(Strategy.name == "Price Action"))
    rule = AlertRule(
        name="Trend breakout (demo data)",
        strategy_id=strategy.id if strategy else None,
        symbols=list(DEMO_ALERT_SYMBOLS),
        timeframe="H1",
        provider="demo",
        conditions=[
            {"type": "ema_trend", "params": {"fast": 20, "slow": 50}, "confluence": "HTF trend alignment"},
            {"type": "range_breakout", "params": {"bars": 20}, "confluence": "Momentum / volume"},
            {"type": "atr_filter", "params": {"period": 14, "min_atr_pct": 0.05}, "confluence": "Volatility (ATR)"},
        ],
        stop_method="atr",
        atr_multiple=1.5,
        targets_rr=[1.5, 3.0],
        cooldown_minutes=240,
        notify=True,
    )
    db.add(rule)
    db.flush()
    cooldown = timedelta(minutes=rule.cooldown_minutes)
    for symbol in DEMO_ALERT_SYMBOLS:
        bars = market_data.fetch_candles(db, settings, "demo", symbol, rule.timeframe, now=now)
        last_alert: datetime | None = None
        for end in range(len(bars) - 48, len(bars)):
            window = bars[: end + 1]
            closed_at = window[-1].time + market_data.timeframe_delta(rule.timeframe)
            if last_alert is not None and closed_at - last_alert < cooldown:
                continue
            ev = alerts.evaluate(db, settings, rule, symbol, window, closed_at)
            if not ev.found:
                continue
            alert = alerts.record_alert(db, settings, rule, ev, closed_at)
            alert.status = "new" if now - closed_at < timedelta(hours=12) else "seen"
            rule.last_alert_at = max(rule.last_alert_at or closed_at, closed_at)
            last_alert = closed_at
    rule.last_checked_at = now
    db.commit()
