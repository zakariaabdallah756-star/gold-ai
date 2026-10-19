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
    notes: Mapped[str] = mapped_column(Text, default="")

    screenshots: Mapped[list[Screenshot]] = relationship(
        back_populates="trade",
        cascade="all, delete-orphan",
        order_by="Screenshot.id",
    )

    @property
    def close_time(self) -> datetime:
        """Timestamp used to attribute realised P&L to a day."""
        return self.exit_time or self.entry_time


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
