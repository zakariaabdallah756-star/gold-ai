"""First-run defaults: a sensible starting trading plan the user can edit."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from .constants import DEFAULT_MISTAKES
from .models import (
    AccountSettings,
    ChecklistItem,
    Confluence,
    Market,
    MistakeType,
    Strategy,
    TradingSession,
)
from .services.partners import seed_partners

DEFAULT_MARKETS = [
    # symbol, name, class, base, quote, contract, pip size, min lot, step, max lot
    ("XAUUSD", "Gold Spot", "metal", "XAU", "USD", 100, 0.1, 0.01, 0.01, 100),
    ("XAGUSD", "Silver Spot", "metal", "XAG", "USD", 5_000, 0.01, 0.01, 0.01, 100),
    ("EURUSD", "Euro / US Dollar", "forex", "EUR", "USD", 100_000, 0.0001, 0.01, 0.01, 100),
    ("GBPUSD", "British Pound / US Dollar", "forex", "GBP", "USD", 100_000, 0.0001, 0.01, 0.01, 100),
    ("USDJPY", "US Dollar / Japanese Yen", "forex", "USD", "JPY", 100_000, 0.01, 0.01, 0.01, 100),
    ("US30", "Dow Jones 30", "index", None, "USD", 1, 1.0, 0.1, 0.1, 500),
    ("NAS100", "Nasdaq 100", "index", None, "USD", 1, 1.0, 0.1, 0.1, 500),
    ("SPX500", "S&P 500", "index", None, "USD", 1, 1.0, 0.1, 0.1, 500),
    ("GER40", "DAX 40", "index", None, "EUR", 1, 1.0, 0.1, 0.1, 500),
    ("BTCUSD", "Bitcoin / US Dollar", "crypto", "BTC", "USD", 1, 1.0, 0.01, 0.01, 50),
]

DEFAULT_SESSIONS = [
    ("Asia", "23:00", "08:00"),
    ("London", "07:00", "16:00"),
    ("New York", "12:00", "21:00"),
]

DEFAULT_STRATEGIES = [
    (
        "Price Action",
        "Pin bars, engulfing candles and inside bars at key levels.",
        "H4, H1, M15",
        "Trade only at pre-marked key levels in the direction of the HTF trend. "
        "Entry on candle close; stop beyond the signal candle.",
        2,
    ),
    (
        "SMC",
        "Smart Money Concepts: liquidity, structure, order blocks and FVGs.",
        "H4, M15, M5",
        "HTF bias -> liquidity sweep -> change of character on LTF -> entry on "
        "order block / FVG retest. Stop beyond the swept extreme.",
        3,
    ),
    (
        "Breakout",
        "Range or session-high/low breakouts with momentum confirmation.",
        "H1, M15",
        "Clear consolidation, break with a strong body close, enter on the "
        "retest. Avoid breakouts into high-impact news.",
        2,
    ),
]

DEFAULT_CONFLUENCES = [
    ("HTF trend alignment", "Trade direction agrees with the D1/H4 trend."),
    ("Key level (S/R)", "Reaction at a pre-marked support/resistance level."),
    ("Liquidity sweep", "Stops taken above/below a prior high/low."),
    ("Break of structure", "BOS / CHoCH confirming the new direction."),
    ("Order block / FVG", "Entry from an order block or fair value gap."),
    ("Session open / killzone", "Entry during London or New York killzone."),
    ("Fibonacci OTE", "Entry in the 61.8%-78.6% retracement zone."),
    ("Momentum / volume", "Strong displacement or volume confirmation."),
]

DEFAULT_CHECKLIST = [
    ("Macro", "Review economic calendar - note high-impact news and times", True),
    ("Macro", "Check macro drivers (DXY, yields, risk sentiment)", False),
    ("HTF Analysis", "Higher timeframe analysis (W1/D1/H4) - define directional bias", True),
    ("Key Levels", "Mark key levels: PDH/PDL, weekly open, S/R, liquidity pools", True),
    ("Plan", "Define today's setups, entry triggers and invalidation", True),
    ("Risk", "Confirm risk per trade and max daily loss for today", True),
    ("Mindset", "Mental state check - rested, calm, no revenge mindset", True),
]


def seed_defaults(session: Session) -> None:
    """Insert the default plan on first run. Safe to call on every startup."""
    if session.get(AccountSettings, 1) is None:
        session.add(AccountSettings(id=1))

    if session.scalar(select(Market.id).limit(1)) is None:
        for symbol, name, cls, base, quote, contract, pip, min_lot, step, max_lot in DEFAULT_MARKETS:
            session.add(
                Market(
                    symbol=symbol,
                    name=name,
                    asset_class=cls,
                    base_currency=base,
                    quote_currency=quote,
                    contract_size=contract,
                    pip_size=pip,
                    min_lot=min_lot,
                    lot_step=step,
                    max_lot=max_lot,
                )
            )

    if session.scalar(select(TradingSession.id).limit(1)) is None:
        for name, start, end in DEFAULT_SESSIONS:
            session.add(TradingSession(name=name, start_utc=start, end_utc=end, active=name != "Asia"))

    if session.scalar(select(Strategy.id).limit(1)) is None:
        for name, description, timeframes, rules, min_conf in DEFAULT_STRATEGIES:
            session.add(
                Strategy(
                    name=name,
                    description=description,
                    timeframes=timeframes,
                    rules=rules,
                    min_confluences=min_conf,
                )
            )

    if session.scalar(select(Confluence.id).limit(1)) is None:
        for name, description in DEFAULT_CONFLUENCES:
            session.add(Confluence(name=name, description=description))

    if session.scalar(select(ChecklistItem.id).limit(1)) is None:
        for order, (category, label, required) in enumerate(DEFAULT_CHECKLIST):
            session.add(ChecklistItem(category=category, label=label, required=required, sort_order=order))

    if session.scalar(select(MistakeType.id).limit(1)) is None:
        for name, description in DEFAULT_MISTAKES:
            session.add(MistakeType(name=name, description=description))

    seed_partners(session)
    session.commit()
