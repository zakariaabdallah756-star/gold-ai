"""Synthetic demo journal used by ``--demo`` (written to a separate data dir).

Trades are generated through the regular trade service so every derived
field and guardrail annotation is produced exactly as for real entries.
Strategies and emotional states are given different edges on purpose, so
the breakdown tables have something meaningful to show.
"""

from __future__ import annotations

import random
from datetime import date, datetime, time, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import AccountSettings, ChecklistDay, ChecklistItem, Confluence, Market, Trade
from ..schemas import TradeIn
from .sizing import round_lots
from .trades import create_trade

# symbol: (weight, start price, typical stop range in pips/points, commission per lot)
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


def _weighted(rng: random.Random, table: dict) -> str:
    names = list(table)
    return rng.choices(names, weights=[table[name][0] for name in names])[0]


def seed_demo(db: Session, today: date, trading_days: int = 80, seed: int = 11) -> int:
    """Populate an empty journal with demo trades. Returns trades created."""
    if db.scalar(select(Trade.id).limit(1)) is not None:
        return 0

    rng = random.Random(seed)
    settings = db.get(AccountSettings, 1)
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
    days.append(today)

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

        trades_today = 1 if day == today else rng.choices([0, 1, 2, 3], weights=[15, 45, 30, 10])[0]
        for _ in range(trades_today):
            symbol = _weighted(rng, INSTRUMENTS)
            market = markets[symbol]
            _, _, stop_range, commission = INSTRUMENTS[symbol]
            prices[symbol] *= 1 + rng.gauss(0, 0.006)
            entry = round(prices[symbol], 5 if market.pip_size < 0.01 else 2)

            strategy = rng.choice(list(STRATEGIES))
            base_wr, rr_range = STRATEGIES[strategy]
            emotion = _weighted(rng, EMOTIONS)
            _, wr_adjust, break_chance = EMOTIONS[emotion]
            # Today's trade is a live open position, so it must pass the guardrails.
            broke_rules = rng.random() < break_chance and day != today

            session = rng.choices(["London", "New York", "Asia"], weights=[50, 42, 8])[0]
            start_hour, end_hour = SESSION_HOURS_UTC[session]
            entry_time = datetime.combine(day, time(rng.randint(start_hour, end_hour), rng.choice([0, 15, 30, 45])))

            direction = rng.choice(["long", "short"])
            sign = 1 if direction == "long" else -1
            stop_pips = rng.uniform(*stop_range)
            stop = entry - sign * stop_pips * market.pip_size
            rr = rng.uniform(1.1, 1.8) if broke_rules and rng.random() < 0.5 else rng.uniform(*rr_range)
            if day == today:
                rr = max(rr, settings.min_rr)
            take_profit = entry + sign * stop_pips * rr * market.pip_size

            pip_value = market.contract_size * market.pip_size * (1.0 / entry if symbol == "USDJPY" else 1.0)
            lots = round_lots(equity * 0.01 / (stop_pips * pip_value), market) or market.min_lot

            roll = rng.random()
            win_probability = base_wr + wr_adjust - (0.08 if broke_rules else 0.0)
            if roll < win_probability:
                realised_r = rr if rng.random() < 0.7 else rng.uniform(0.8, rr)
            elif roll < win_probability + 0.07:
                realised_r = 0.0
            else:
                realised_r = -1.0 if rng.random() < 0.8 else -rng.uniform(0.4, 0.9)
            exit_price = entry + sign * stop_pips * realised_r * market.pip_size
            outcome = "win" if realised_r > 0 else "loss" if realised_r < 0 else "breakeven"

            is_open = day == today
            picked = rng.sample(confluences, k=rng.randint(1, 2) if broke_rules else rng.randint(3, 4))
            digits = 5 if market.pip_size < 0.01 else 3 if market.pip_size < 1 else 1
            trade_in = TradeIn(
                status="open" if is_open else "closed",
                entry_time=entry_time,
                exit_time=None if is_open else entry_time + timedelta(minutes=rng.randint(15, 300)),
                symbol=symbol,
                direction=direction,
                entry_price=round(entry, digits),
                stop_loss=round(stop, digits),
                take_profit=round(take_profit, digits),
                exit_price=None if is_open else round(exit_price, digits),
                position_size=lots,
                fees=round(commission * lots, 2),
                strategy=strategy,
                timeframe=rng.choice(["M5", "M15", "H1"]),
                session=session,
                confluences=picked,
                emotion=emotion,
                rule_compliant=False if broke_rules else None,
                tags=["demo"] + (["a-plus-setup"] if rng.random() < 0.2 else []),
                notes=rng.choice(NOTES[outcome]),
            )
            trade, _ = create_trade(db, settings, trade_in)
            if trade.pnl is not None:
                equity += trade.pnl
            created += 1
    return created
