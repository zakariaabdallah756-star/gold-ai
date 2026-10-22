"""SQLAlchemy ORM models.

Design notes
------------
* Trades store ``symbol``, ``strategy``, ``session`` and ``emotion`` as plain
  strings instead of foreign keys. Renaming or deleting an item of the trading
  plan must never rewrite or orphan journal history, and plain strings keep
  CSV/JSON exports self-describing.
* Trade times are *naive* datetimes expressed in the account time zone
  (``AccountSettings.timezone``) - the same wall-clock time the trader sees.
* Derived trade metrics (R:R, R-multiple, outcome, auto P&L) are persisted so
  that exports and sorting work without recomputation; they are refreshed by
  ``services.trades`` on every create/update.
"""

from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())


class AccountSettings(TimestampMixin, Base):
    """Singleton row (id=1) holding account data and the hard risk rules."""

    __tablename__ = "account_settings"

    id: Mapped[int] = mapped_column(primary_key=True)
    account_name: Mapped[str] = mapped_column(String(80), default="Main Account")
    currency: Mapped[str] = mapped_column(String(3), default="USD")
    starting_balance: Mapped[float] = mapped_column(Float, default=10_000.0)
    timezone: Mapped[str] = mapped_column(String(64), default="UTC")
    language: Mapped[str] = mapped_column(String(5), default="en")  # en | it - UI, messages and AI answers

    # Per-trade rules
    default_risk_pct: Mapped[float] = mapped_column(Float, default=1.0)
    max_risk_pct: Mapped[float] = mapped_column(Float, default=2.0)
    min_rr: Mapped[float] = mapped_column(Float, default=2.0)
    trade_rule_mode: Mapped[str] = mapped_column(String(8), default="block")  # block | warn

    # Daily rules
    max_daily_loss_pct: Mapped[float] = mapped_column(Float, default=3.0)
    max_trades_per_day: Mapped[int] = mapped_column(Integer, default=0)  # 0 = disabled
    daily_limit_mode: Mapped[str] = mapped_column(String(8), default="lock")  # lock | warn

    # Process rules
    require_checklist: Mapped[bool] = mapped_column(Boolean, default=True)
    warn_outside_sessions: Mapped[bool] = mapped_column(Boolean, default=True)
    session_rule_mode: Mapped[str] = mapped_column(String(8), default="warn")  # warn | block (off-session entries)
    fomo_guard: Mapped[bool] = mapped_column(Boolean, default=True)  # anti-FOMO warnings on Vision AI ideas

    # Economic news guardrail
    news_mode: Mapped[str] = mapped_column(String(8), default="warn")  # off | warn | block
    news_before_min: Mapped[int] = mapped_column(Integer, default=30)
    news_after_min: Mapped[int] = mapped_column(Integer, default=30)
    news_min_impact: Mapped[str] = mapped_column(String(8), default="high")  # low | medium | high
    news_match_currency: Mapped[bool] = mapped_column(Boolean, default=True)

    # Emergency kill switch (cool-off). Set via /api/killswitch, not the settings form.
    cooloff_until: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    cooloff_started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    # Default plan groups already seeded once (deleting defaults must not bring them back on restart).
    seeded_defaults: Mapped[list[str]] = mapped_column(JSON, default=list)
    cooloff_reason: Mapped[str] = mapped_column(String(200), default="")

    # Position-management defaults for new trades / signals
    default_auto_be: Mapped[bool] = mapped_column(Boolean, default=True)
    default_trail_pips: Mapped[float] = mapped_column(Float, default=0.0)  # 0 = no trailing

    # Risk disclaimer acceptance (first-launch agreement)
    disclaimer_accepted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    disclaimer_version: Mapped[str] = mapped_column(String(20), default="")

    # AI assistant (Vision AI). The key never leaves this machine and is not exported in backups;
    # ANTHROPIC_API_KEY (or an ``ant auth login`` profile) is used when it is empty.
    ai_model: Mapped[str] = mapped_column(String(40), default="claude-opus-5-5")
    ai_effort: Mapped[str] = mapped_column(String(8), default="high")  # low | medium | high | xhigh | max
    ai_api_key: Mapped[str] = mapped_column(String(200), default="")

    # Notifications (push to phone / browser). Channel secrets live in NotificationChannel.config.
    notifications_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    notify_events: Mapped[list[str]] = mapped_column(JSON, default=lambda: ["strategy_alert", "risk", "news"])
    news_reminder_min: Mapped[int] = mapped_column(Integer, default=15)  # minutes before a high-impact release
    # Hold strategy alerts and news reminders while no active session is open (risk events always go out).
    notify_quiet_outside_sessions: Mapped[bool] = mapped_column(Boolean, default=False)
    app_url: Mapped[str] = mapped_column(String(200), default="")  # link opened from a notification
    # Web Push (VAPID) key pair, generated on first use. Both stay on this machine (not in backups).
    vapid_public_key: Mapped[str] = mapped_column(String(200), default="")
    vapid_private_key: Mapped[str] = mapped_column(Text, default="")
    vapid_contact: Mapped[str] = mapped_column(String(120), default="mailto:trading-os@example.com")

    # Strategy alert engine (background scanner). Provider API keys are local secrets (not in backups).
    scanner_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    scanner_interval_sec: Mapped[int] = mapped_column(Integer, default=60)
    market_api_keys: Mapped[dict] = mapped_column(JSON, default=dict)  # provider -> API key


class Market(TimestampMixin, Base):
    """A tradable instrument and the contract specification used for sizing."""

    __tablename__ = "markets"

    id: Mapped[int] = mapped_column(primary_key=True)
    symbol: Mapped[str] = mapped_column(String(20), unique=True)
    name: Mapped[str] = mapped_column(String(80), default="")
    asset_class: Mapped[str] = mapped_column(String(20), default="forex")
    base_currency: Mapped[str | None] = mapped_column(String(3), nullable=True)
    quote_currency: Mapped[str] = mapped_column(String(3), default="USD")
    contract_size: Mapped[float] = mapped_column(Float, default=100_000.0)
    pip_size: Mapped[float] = mapped_column(Float, default=0.0001)
    min_lot: Mapped[float] = mapped_column(Float, default=0.01)
    lot_step: Mapped[float] = mapped_column(Float, default=0.01)
    max_lot: Mapped[float] = mapped_column(Float, default=100.0)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    notes: Mapped[str] = mapped_column(Text, default="")


class TradingSession(TimestampMixin, Base):
    """A market session window, defined in UTC (``HH:MM``). May wrap midnight."""

    __tablename__ = "trading_sessions"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(40), unique=True)
    start_utc: Mapped[str] = mapped_column(String(5))
    end_utc: Mapped[str] = mapped_column(String(5))
    active: Mapped[bool] = mapped_column(Boolean, default=True)  # part of my trading window


class Strategy(TimestampMixin, Base):
    __tablename__ = "strategies"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(60), unique=True)
    description: Mapped[str] = mapped_column(Text, default="")
    timeframes: Mapped[str] = mapped_column(String(60), default="")
    rules: Mapped[str] = mapped_column(Text, default="")
    min_confluences: Mapped[int] = mapped_column(Integer, default=0)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    # live | incubator (in test - backtest before trading it live) | retired
    status: Mapped[str] = mapped_column(String(10), default="live")

    # Strategy Co-Pilot parameters (services.copilot); empty lists mean "any".
    required_confluences: Mapped[list[str]] = mapped_column(JSON, default=list)
    allowed_sessions: Mapped[list[str]] = mapped_column(JSON, default=list)
    allowed_timeframes: Mapped[list[str]] = mapped_column(JSON, default=list)
    allowed_directions: Mapped[str] = mapped_column(String(5), default="both")  # both | long | short
    min_rr: Mapped[float | None] = mapped_column(Float, nullable=True)  # stricter than the account minimum
    require_bias_alignment: Mapped[bool] = mapped_column(Boolean, default=False)

    # Backtests: money per 1R used to express R-multiples in currency (None = account default risk).
    backtest_risk_per_trade: Mapped[float | None] = mapped_column(Float, nullable=True)
    # Names this strategy had before a rename: journal trades keep the name they were logged with, and the
    # backtest-vs-live comparison still counts them (services.backtest.strategy_renamed).
    former_names: Mapped[list[str]] = mapped_column(JSON, default=list)


class Confluence(TimestampMixin, Base):
    __tablename__ = "confluences"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(80), unique=True)
    description: Mapped[str] = mapped_column(Text, default="")
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class ChecklistItem(TimestampMixin, Base):
    """A template item of the pre-market routine."""

    __tablename__ = "checklist_items"

    id: Mapped[int] = mapped_column(primary_key=True)
    label: Mapped[str] = mapped_column(String(160))
    category: Mapped[str] = mapped_column(String(40), default="General")
    required: Mapped[bool] = mapped_column(Boolean, default=True)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class ChecklistDay(TimestampMixin, Base):
    """The pre-market routine as completed on a specific day."""

    __tablename__ = "checklist_days"

    day: Mapped[date] = mapped_column(Date, primary_key=True)
    checked_item_ids: Mapped[list[int]] = mapped_column(JSON, default=list)
    bias: Mapped[str] = mapped_column(String(16), default="")  # bullish | bearish | neutral
    key_levels: Mapped[str] = mapped_column(Text, default="")
    news: Mapped[str] = mapped_column(Text, default="")
    notes: Mapped[str] = mapped_column(Text, default="")
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class Trade(TimestampMixin, Base):
    __tablename__ = "trades"

    id: Mapped[int] = mapped_column(primary_key=True)
    status: Mapped[str] = mapped_column(String(8), default="open")  # open | closed
    entry_time: Mapped[datetime] = mapped_column(DateTime, index=True)
    exit_time: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    symbol: Mapped[str] = mapped_column(String(20), index=True)
    direction: Mapped[str] = mapped_column(String(5))  # long | short
    entry_price: Mapped[float] = mapped_column(Float)
    stop_loss: Mapped[float] = mapped_column(Float)  # initial stop
    take_profit: Mapped[float | None] = mapped_column(Float, nullable=True)
    exit_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    position_size: Mapped[float | None] = mapped_column(Float, nullable=True)  # lots
    risk_amount: Mapped[float | None] = mapped_column(Float, nullable=True)
    fees: Mapped[float] = mapped_column(Float, default=0.0)
    pnl: Mapped[float | None] = mapped_column(Float, nullable=True)  # net, account currency
    pnl_manual: Mapped[bool] = mapped_column(Boolean, default=False)

    # Derived (persisted for export/sorting)
    planned_rr: Mapped[float | None] = mapped_column(Float, nullable=True)
    executed_rr: Mapped[float | None] = mapped_column(Float, nullable=True)
    r_multiple: Mapped[float | None] = mapped_column(Float, nullable=True)
    outcome: Mapped[str | None] = mapped_column(String(10), nullable=True)  # win|loss|breakeven

    strategy: Mapped[str] = mapped_column(String(60), default="")
    timeframe: Mapped[str] = mapped_column(String(10), default="")
    session: Mapped[str] = mapped_column(String(40), default="")
    confluences: Mapped[list[str]] = mapped_column(JSON, default=list)
    emotion: Mapped[str] = mapped_column(String(20), default="")
    rule_compliant: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    violations: Mapped[list[dict]] = mapped_column(JSON, default=list)
    tags: Mapped[list[str]] = mapped_column(JSON, default=list)
    mistakes: Mapped[list[str]] = mapped_column(JSON, default=list)
    notes: Mapped[str] = mapped_column(Text, default="")
    source: Mapped[str] = mapped_column(String(12), default="manual")  # manual | signal | calculator
    signal_text: Mapped[str] = mapped_column(Text, default="")

    # Position management. ``managed`` is True once the trade has fill events
    # (partial/full closes recorded in ``events``); its exit, P&L and status are
    # then derived from that ledger by ``services.positions``.
    managed: Mapped[bool] = mapped_column(Boolean, default=False)
    current_stop: Mapped[float | None] = mapped_column(Float, nullable=True)  # active stop (BE / trailed)
    remaining_size: Mapped[float | None] = mapped_column(Float, nullable=True)  # lots still open
    realized_pnl: Mapped[float] = mapped_column(Float, default=0.0)  # sum of fill P&L, before fees
    remaining_risk: Mapped[float | None] = mapped_column(Float, nullable=True)  # money lost if stopped now
    be_enabled: Mapped[bool] = mapped_column(Boolean, default=False)  # stop -> entry when TP1 is hit
    be_offset_pips: Mapped[float] = mapped_column(Float, default=0.0)  # lock in +x pips at break-even
    trail_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    trail_distance_pips: Mapped[float | None] = mapped_column(Float, nullable=True)
    trail_trigger: Mapped[str] = mapped_column(String(10), default="tp1")  # tp1 | immediate
    best_price: Mapped[float | None] = mapped_column(Float, nullable=True)  # most favourable price seen
    worst_price: Mapped[float | None] = mapped_column(Float, nullable=True)  # most adverse price seen
    last_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    last_price_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    screenshots: Mapped[list[Screenshot]] = relationship(
        back_populates="trade",
        cascade="all, delete-orphan",
        order_by="Screenshot.id",
    )
    targets: Mapped[list[TradeTarget]] = relationship(
        back_populates="trade",
        cascade="all, delete-orphan",
        order_by="TradeTarget.level",
    )
    events: Mapped[list[TradeEvent]] = relationship(
        back_populates="trade",
        cascade="all, delete-orphan",
        order_by="(TradeEvent.time, TradeEvent.id)",
    )

    @property
    def close_time(self) -> datetime:
        """Timestamp used to attribute realised P&L to a day."""
        return self.exit_time or self.entry_time

    @property
    def risk_free(self) -> bool:
        """True when the active stop is at or beyond the entry (no money at risk)."""
        stop = self.current_stop if self.current_stop is not None else self.stop_loss
        return stop >= self.entry_price if self.direction == "long" else stop <= self.entry_price


class TradeTarget(Base):
    """A take-profit level with the share of the *original* size closed there."""

    __tablename__ = "trade_targets"

    id: Mapped[int] = mapped_column(primary_key=True)
    trade_id: Mapped[int] = mapped_column(ForeignKey("trades.id", ondelete="CASCADE"), index=True)
    level: Mapped[int] = mapped_column(Integer)  # 1 = TP1 (nearest)
    price: Mapped[float] = mapped_column(Float)
    close_pct: Mapped[float] = mapped_column(Float)

    trade: Mapped[Trade] = relationship(back_populates="targets")

    @property
    def hit(self) -> bool:
        return any(e.kind == "tp" and e.target_level == self.level for e in self.trade.events)

    @property
    def rr(self) -> float | None:
        risk = abs(self.trade.entry_price - self.trade.stop_loss)
        return abs(self.price - self.trade.entry_price) / risk if risk else None


class TradeEvent(Base):
    """Position-management ledger entry.

    Fills (``lots`` > 0): ``tp`` (target hit), ``partial`` (manual partial close),
    ``stop`` (stop hit), ``close`` (manual close of the remainder).
    Stop moves (``stop_before``/``stop_after``): ``breakeven``, ``trail``, ``stop_move``.
    """

    __tablename__ = "trade_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    trade_id: Mapped[int] = mapped_column(ForeignKey("trades.id", ondelete="CASCADE"), index=True)
    time: Mapped[datetime] = mapped_column(DateTime, index=True)
    kind: Mapped[str] = mapped_column(String(12))
    price: Mapped[float | None] = mapped_column(Float, nullable=True)
    lots: Mapped[float | None] = mapped_column(Float, nullable=True)
    pnl: Mapped[float | None] = mapped_column(Float, nullable=True)  # fill P&L before fees
    pnl_manual: Mapped[bool] = mapped_column(Boolean, default=False)
    target_level: Mapped[int | None] = mapped_column(Integer, nullable=True)
    stop_before: Mapped[float | None] = mapped_column(Float, nullable=True)
    stop_after: Mapped[float | None] = mapped_column(Float, nullable=True)
    note: Mapped[str] = mapped_column(String(200), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    trade: Mapped[Trade] = relationship(back_populates="events")

    @property
    def is_fill(self) -> bool:
        return bool(self.lots)


class Screenshot(Base):
    """A chart image attached to a trade: an uploaded file or an external URL."""

    __tablename__ = "screenshots"

    id: Mapped[int] = mapped_column(primary_key=True)
    trade_id: Mapped[int] = mapped_column(ForeignKey("trades.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(4))  # file | url
    filename: Mapped[str | None] = mapped_column(String(80), nullable=True)
    url: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    caption: Mapped[str] = mapped_column(String(200), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    trade: Mapped[Trade] = relationship(back_populates="screenshots")

    @property
    def src(self) -> str:
        return f"/uploads/{self.filename}" if self.kind == "file" else (self.url or "")


class NewsEvent(TimestampMixin, Base):
    """A scheduled economic release. ``time`` is account-local wall-clock time."""

    __tablename__ = "news_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    time: Mapped[datetime] = mapped_column(DateTime, index=True)
    title: Mapped[str] = mapped_column(String(120))
    currency: Mapped[str] = mapped_column(String(3), default="USD")  # "ALL" = affects every market
    impact: Mapped[str] = mapped_column(String(8), default="high")  # low | medium | high
    before_min: Mapped[int | None] = mapped_column(Integer, nullable=True)  # None = settings default
    after_min: Mapped[int | None] = mapped_column(Integer, nullable=True)
    notes: Mapped[str] = mapped_column(String(200), default="")


class DailyReview(TimestampMixin, Base):
    """End-of-day self-reflection (post-market review)."""

    __tablename__ = "daily_reviews"

    day: Mapped[date] = mapped_column(Date, primary_key=True)
    discipline_rating: Mapped[int | None] = mapped_column(Integer, nullable=True)  # 1-5
    mood: Mapped[str] = mapped_column(String(20), default="")
    followed_plan: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    went_well: Mapped[str] = mapped_column(Text, default="")
    went_wrong: Mapped[str] = mapped_column(Text, default="")
    lessons: Mapped[str] = mapped_column(Text, default="")
    tomorrow_focus: Mapped[str] = mapped_column(Text, default="")
    mistakes: Mapped[list[str]] = mapped_column(JSON, default=list)


class MistakeType(TimestampMixin, Base):
    """A psychological / execution mistake that can be tagged on trades and reviews."""

    __tablename__ = "mistake_types"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(60), unique=True)
    description: Mapped[str] = mapped_column(Text, default="")
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class BacktestTrade(TimestampMixin, Base):
    """A historical (backtest / forward-test) trade of a strategy - never part of the live journal."""

    __tablename__ = "backtest_trades"

    id: Mapped[int] = mapped_column(primary_key=True)
    strategy_id: Mapped[int] = mapped_column(ForeignKey("strategies.id", ondelete="CASCADE"), index=True)
    trade_time: Mapped[datetime] = mapped_column(DateTime, index=True)
    symbol: Mapped[str] = mapped_column(String(20), default="")
    direction: Mapped[str] = mapped_column(String(5))  # long | short
    entry_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    stop_loss: Mapped[float | None] = mapped_column(Float, nullable=True)
    take_profit: Mapped[float | None] = mapped_column(Float, nullable=True)
    exit_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    r_multiple: Mapped[float | None] = mapped_column(Float, nullable=True)  # from prices, or entered directly
    planned_rr: Mapped[float | None] = mapped_column(Float, nullable=True)
    pnl: Mapped[float | None] = mapped_column(Float, nullable=True)  # optional money result
    outcome: Mapped[str | None] = mapped_column(String(10), nullable=True)
    session: Mapped[str] = mapped_column(String(40), default="")
    timeframe: Mapped[str] = mapped_column(String(10), default="")
    tags: Mapped[list[str]] = mapped_column(JSON, default=list)
    notes: Mapped[str] = mapped_column(Text, default="")


class PropChallenge(TimestampMixin, Base):
    """A prop-firm challenge / funded account tracked against the firm's risk rules."""

    __tablename__ = "prop_challenges"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(80))
    firm: Mapped[str] = mapped_column(String(60), default="")
    preset: Mapped[str] = mapped_column(String(40), default="custom")
    phase: Mapped[str] = mapped_column(String(12), default="phase1")  # phase1 | phase2 | funded | custom
    account_size: Mapped[float] = mapped_column(Float)
    profit_target_pct: Mapped[float | None] = mapped_column(Float, nullable=True)  # None for funded accounts
    max_daily_loss_pct: Mapped[float] = mapped_column(Float, default=5.0)
    max_total_drawdown_pct: Mapped[float] = mapped_column(Float, default=10.0)
    drawdown_mode: Mapped[str] = mapped_column(String(10), default="static")  # static | trailing
    min_trading_days: Mapped[int] = mapped_column(Integer, default=0)
    max_calendar_days: Mapped[int | None] = mapped_column(Integer, nullable=True)  # None = no time limit
    start_date: Mapped[date] = mapped_column(Date)
    end_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    status: Mapped[str] = mapped_column(String(10), default="active")  # active | passed | failed | archived
    active: Mapped[bool] = mapped_column(Boolean, default=False)  # the one challenge driving "prop mode"
    enforce: Mapped[bool] = mapped_column(Boolean, default=True)  # block live entries that break the rules
    notes: Mapped[str] = mapped_column(Text, default="")


class Partner(TimestampMixin, Base):
    """A broker or prop firm shown on the Recommended Partners page."""

    __tablename__ = "partners"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(80), unique=True)
    kind: Mapped[str] = mapped_column(String(10), default="broker")  # broker | prop_firm
    website_url: Mapped[str] = mapped_column(String(300), default="")
    link_url: Mapped[str] = mapped_column(String(500), default="")  # custom link shown instead of the website
    description: Mapped[str] = mapped_column(Text, default="")
    highlights: Mapped[list[str]] = mapped_column(JSON, default=list)
    featured: Mapped[bool] = mapped_column(Boolean, default=False)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class VisionAnalysis(TimestampMixin, Base):
    """An AI analysis of a chart screenshot (image stored in the uploads folder)."""

    __tablename__ = "vision_analyses"

    id: Mapped[int] = mapped_column(primary_key=True)
    filename: Mapped[str] = mapped_column(String(80))
    image_width: Mapped[int | None] = mapped_column(Integer, nullable=True)
    image_height: Mapped[int | None] = mapped_column(Integer, nullable=True)
    symbol: Mapped[str] = mapped_column(String(20), default="")
    timeframe: Mapped[str] = mapped_column(String(10), default="")
    strategy: Mapped[str] = mapped_column(String(60), default="")
    user_notes: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(10), default="pending")  # pending | done | error
    model: Mapped[str] = mapped_column(String(40), default="")
    analysis: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    error: Mapped[str] = mapped_column(Text, default="")
    trade_id: Mapped[int | None] = mapped_column(ForeignKey("trades.id", ondelete="SET NULL"), nullable=True)

    messages: Mapped[list[VisionMessage]] = relationship(
        back_populates="analysis_record",
        cascade="all, delete-orphan",
        order_by="VisionMessage.id",
    )


class VisionMessage(Base):
    """One Q&A turn about an analysed chart. ``content`` holds the API content blocks (replayed unchanged)."""

    __tablename__ = "vision_messages"

    id: Mapped[int] = mapped_column(primary_key=True)
    analysis_id: Mapped[int] = mapped_column(ForeignKey("vision_analyses.id", ondelete="CASCADE"), index=True)
    role: Mapped[str] = mapped_column(String(10))  # user | assistant
    text: Mapped[str] = mapped_column(Text, default="")  # display text
    content: Mapped[list[dict]] = mapped_column(JSON, default=list)  # raw blocks incl. thinking (assistant)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    analysis_record: Mapped[VisionAnalysis] = relationship(back_populates="messages")


class NotificationChannel(TimestampMixin, Base):
    """A delivery target: a browser/phone Web Push subscription or a push service / webhook.

    ``config`` holds the channel's secrets (bot tokens, topics, webhook URLs, push
    subscription keys). It is never returned in full by the API and is not exported
    in backups.
    """

    __tablename__ = "notification_channels"

    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String(12))  # webpush | ntfy | telegram | discord | slack | pushover | webhook
    name: Mapped[str] = mapped_column(String(80), default="")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    config: Mapped[dict] = mapped_column(JSON, default=dict)
    events: Mapped[list[str]] = mapped_column(JSON, default=list)  # empty = every enabled event type
    last_sent_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_error: Mapped[str] = mapped_column(String(300), default="")


class NotificationLog(Base):
    """One notification and its delivery result per channel."""

    __tablename__ = "notification_log"

    id: Mapped[int] = mapped_column(primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, index=True)  # account-local
    event: Mapped[str] = mapped_column(String(30))  # strategy_alert | risk | news | test
    title: Mapped[str] = mapped_column(String(200), default="")
    body: Mapped[str] = mapped_column(Text, default="")
    data: Mapped[dict] = mapped_column(JSON, default=dict)
    url: Mapped[str] = mapped_column(String(300), default="")
    status: Mapped[str] = mapped_column(String(10), default="sent")  # sent | partial | failed | skipped
    results: Mapped[list[dict]] = mapped_column(JSON, default=list)  # [{channel_id, kind, name, ok, error}]


class AlertRule(TimestampMixin, Base):
    """A scanner rule: watch symbols on a timeframe and alert when a strategy's conditions line up."""

    __tablename__ = "alert_rules"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(80))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    strategy_id: Mapped[int | None] = mapped_column(
        ForeignKey("strategies.id", ondelete="CASCADE"), nullable=True, index=True
    )
    symbols: Mapped[list[str]] = mapped_column(JSON, default=list)
    timeframe: Mapped[str] = mapped_column(String(4), default="M15")  # M1 M5 M15 M30 H1 H4 D1
    provider: Mapped[str] = mapped_column(String(16), default="yahoo")  # market data source
    provider_symbols: Mapped[dict] = mapped_column(JSON, default=dict)  # journal symbol -> provider symbol
    direction: Mapped[str] = mapped_column(String(5), default="both")  # both | long | short
    # [{"type": "liquidity_sweep", "params": {...}, "confluence": "Liquidity sweep"}, ...]
    conditions: Mapped[list[dict]] = mapped_column(JSON, default=list)
    min_conditions: Mapped[int] = mapped_column(Integer, default=0)  # 0 = every condition must hold
    stop_method: Mapped[str] = mapped_column(String(10), default="swing")  # swing | atr | signal
    stop_buffer_pips: Mapped[float] = mapped_column(Float, default=0.0)
    atr_multiple: Mapped[float] = mapped_column(Float, default=1.5)
    targets_rr: Mapped[list[float]] = mapped_column(JSON, default=list)  # empty = one target at the minimum R:R
    risk_pct: Mapped[float | None] = mapped_column(Float, nullable=True)  # None = account default
    cooldown_minutes: Mapped[int] = mapped_column(Integer, default=60)
    notify: Mapped[bool] = mapped_column(Boolean, default=True)
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_candle_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_alert_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_error: Mapped[str] = mapped_column(String(300), default="")
    # Per symbol: {"through": close time of the newest evaluated candle, "alerted": time of the last alert}
    # (ISO strings). Survives deleted alerts, so a deleted alert is never sent again.
    scan_state: Mapped[dict] = mapped_column(JSON, default=dict)


class StrategyAlert(Base):
    """A setup found by the scanner. Informational only - nothing is ever executed automatically."""

    __tablename__ = "strategy_alerts"

    id: Mapped[int] = mapped_column(primary_key=True)
    rule_id: Mapped[int | None] = mapped_column(
        ForeignKey("alert_rules.id", ondelete="SET NULL"), nullable=True, index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, index=True)  # account-local
    candle_time: Mapped[datetime] = mapped_column(DateTime)  # close time of the signal candle
    strategy: Mapped[str] = mapped_column(String(60), default="")
    symbol: Mapped[str] = mapped_column(String(20))
    timeframe: Mapped[str] = mapped_column(String(4), default="")
    direction: Mapped[str] = mapped_column(String(5))  # long | short
    entry_price: Mapped[float] = mapped_column(Float)
    stop_loss: Mapped[float] = mapped_column(Float)
    take_profits: Mapped[list[float]] = mapped_column(JSON, default=list)
    planned_rr: Mapped[float | None] = mapped_column(Float, nullable=True)
    lots: Mapped[float | None] = mapped_column(Float, nullable=True)
    risk_amount: Mapped[float | None] = mapped_column(Float, nullable=True)
    confluences: Mapped[list[str]] = mapped_column(JSON, default=list)
    details: Mapped[dict] = mapped_column(JSON, default=dict)  # condition results, guardrail pre-check
    signal_text: Mapped[str] = mapped_column(Text, default="")  # parseable by the Signals page
    status: Mapped[str] = mapped_column(String(10), default="new")  # new | seen | dismissed | taken
    trade_id: Mapped[int | None] = mapped_column(ForeignKey("trades.id", ondelete="SET NULL"), nullable=True)
    notification_id: Mapped[int | None] = mapped_column(
        ForeignKey("notification_log.id", ondelete="SET NULL"), nullable=True
    )


class Candle(Base):
    """OHLC bars pushed by an external feed (MT4/MT5 script, TradingView webhook) for the ``push`` provider."""

    __tablename__ = "candles"
    __table_args__ = (UniqueConstraint("symbol", "timeframe", "time", name="uq_candle"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    symbol: Mapped[str] = mapped_column(String(20))
    timeframe: Mapped[str] = mapped_column(String(4))
    time: Mapped[datetime] = mapped_column(DateTime)  # bar open time, account-local
    open: Mapped[float] = mapped_column(Float)
    high: Mapped[float] = mapped_column(Float)
    low: Mapped[float] = mapped_column(Float)
    close: Mapped[float] = mapped_column(Float)
    volume: Mapped[float | None] = mapped_column(Float, nullable=True)
