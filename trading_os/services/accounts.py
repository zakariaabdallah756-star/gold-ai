"""Trading accounts: the default account, the account the app shows (scope) and live account metrics.

Every trade belongs to one account (``Trade.account_id``). The *default* account mirrors the settings'
account name, currency and starting balance (one source of truth: the settings), so a journal from before
v1.5 behaves exactly as it did. The *active* account (``AccountSettings.active_account_id``) is the one the
dashboard, journal, calendar and reports show; ``None`` shows every account together. Accounts in another
currency than the settings' (their money cannot be added up), demo accounts (not real money) and archived
accounts are left out of "all accounts" and shown on their own.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from ..models import AccountSettings, Market, PropChallenge, Trade, TradingAccount
from .i18n import tr

KINDS = ("live", "demo", "prop", "funded")
PLATFORMS = ("mt4", "mt5", "ctrader", "tradingview", "api", "other")
CONNECTIONS = ("none", "paper", "webhook", "mt_bridge", "mt5_terminal", "oanda")
STATUSES = ("active", "archived")
SNAPSHOT_FRESH = timedelta(minutes=5)  # a bridge snapshot older than this no longer counts as live
ACTIVE = object()  # "the account the app shows" (settings.active_account_id)


class AccountError(Exception):
    """An account change the journal cannot accept (shown to the trader)."""


@dataclass(frozen=True)
class Scope:
    """Which trades count: ``account_ids`` None = every trade (no filter needed)."""

    account_ids: tuple[int, ...] | None
    account: TradingAccount | None = None  # the single account shown; None = all accounts together
    accounts: tuple[TradingAccount, ...] = field(default_factory=tuple)  # accounts included
    excluded: tuple[TradingAccount, ...] = field(default_factory=tuple)  # other currency / archived

    @property
    def is_all(self) -> bool:
        return self.account is None


# --------------------------------------------------------------------------- the default account


def ensure_default_account(db: Session, settings: AccountSettings) -> TradingAccount:
    """Create the default account on first start (from the settings) and attach older trades to it."""
    account = db.get(TradingAccount, settings.default_account_id) if settings.default_account_id else None
    if account is None:
        account = db.scalar(select(TradingAccount).order_by(TradingAccount.id).limit(1))
    if account is None:
        account = TradingAccount(
            name=settings.account_name or "Main Account",
            kind="live",
            currency=settings.currency,
            starting_balance=settings.starting_balance,
        )
        db.add(account)
        db.flush()
    settings.default_account_id = account.id
    sync_default_from_settings(db, settings)
    db.execute(update(Trade).where(Trade.account_id.is_(None)).values(account_id=account.id))
    if settings.active_account_id is not None and db.get(TradingAccount, settings.active_account_id) is None:
        settings.active_account_id = None
    db.commit()
    return account


def default_account(db: Session, settings: AccountSettings) -> TradingAccount | None:
    return db.get(TradingAccount, settings.default_account_id) if settings.default_account_id else None


def is_default(settings: AccountSettings, account: TradingAccount) -> bool:
    return account.id == settings.default_account_id


def account_label(settings: AccountSettings, account: TradingAccount) -> str:
    """The account's display name (the default account mirrors the settings account name)."""
    return settings.account_name if is_default(settings, account) else account.name


def sync_default_from_settings(db: Session, settings: AccountSettings) -> None:
    """Copy the settings' account name, currency and starting balance to the default account."""
    account = default_account(db, settings)
    if account is None:
        return
    if account.name != settings.account_name:
        clash = db.scalar(
            select(TradingAccount.id).where(
                TradingAccount.name == settings.account_name, TradingAccount.id != account.id
            )
        )
        if clash is None:
            account.name = settings.account_name
    account.currency = settings.currency
    account.starting_balance = settings.starting_balance


def sync_settings_from_default(settings: AccountSettings, account: TradingAccount) -> None:
    """The other direction, after the default account was edited on the Trading Accounts page."""
    settings.account_name = account.name
    settings.currency = account.currency
    settings.starting_balance = account.starting_balance


def currency_of(settings: AccountSettings, account: TradingAccount | None) -> str:
    """An account's currency (the default account follows the settings; no account: the settings')."""
    return account.currency if account is not None and not is_default(settings, account) else settings.currency


def account_for_new_trade(db: Session, settings: AccountSettings, requested: int | None) -> TradingAccount | None:
    """The account a new trade is logged on: the one asked for, else the account shown, else the default."""
    for candidate in (requested, settings.active_account_id, settings.default_account_id):
        if candidate is None:
            continue
        account = db.get(TradingAccount, candidate)
        if account is not None:
            if candidate == requested and account.status != "active":
                raise AccountError(
                    tr("The account '{name}' is archived: reactivate it to log trades on it", name=account.name)
                )
            if account.status == "active":
                return account
        elif candidate == requested:
            raise AccountError(tr("Unknown trading account {id}", id=requested))
    return None


# --------------------------------------------------------------------------- scope


def resolve_scope(db: Session, settings: AccountSettings, account_id=ACTIVE) -> Scope:
    """The trades a view counts. ``account_id``: an id, None (all accounts) or ACTIVE (what the app shows)."""
    if account_id is ACTIVE:
        account_id = settings.active_account_id
    accounts = list(db.scalars(select(TradingAccount).order_by(TradingAccount.id)))
    if account_id is not None:
        chosen = next((a for a in accounts if a.id == account_id), None)
        if chosen is not None:
            return Scope((chosen.id,), chosen, (chosen,), ())
    if not accounts:
        return Scope(None)
    # Real money only: demo/paper accounts, archived accounts and other currencies are shown on their own.
    included = [
        a
        for a in accounts
        if a.status == "active"
        and a.currency == settings.currency
        and (a.kind != "demo" or a.id == settings.default_account_id)  # new trades land on the default
    ]
    if not included:  # nothing comparable: fall back to the default account alone
        included = [a for a in accounts if a.id == settings.default_account_id] or accounts[:1]
    excluded = tuple(a for a in accounts if a not in included)
    ids = None if not excluded else tuple(a.id for a in included)
    return Scope(ids, None, tuple(included), excluded)


def scope_label(settings: AccountSettings, scope: Scope) -> str:
    """What a report says it covers: the account's name, or "All accounts"."""
    if scope.account is None:
        return tr("All accounts") if len(scope.accounts) > 1 else settings.account_name
    return settings.account_name if is_default(settings, scope.account) else scope.account.name


def scope_for_account(db: Session, account_id: int | None) -> Scope:
    """The scope of one account (a trade's own account), or every trade when ``account_id`` is None."""
    if account_id is None:
        return Scope(None)
    account = db.get(TradingAccount, account_id)
    return Scope((account_id,), account, (account,) if account else (), ())


def starting_balance(db: Session, settings: AccountSettings, account_ids: tuple[int, ...] | None = None) -> float:
    """Sum of the starting balances counted by ``account_ids`` (the default account follows the settings)."""
    rows = dict(db.execute(select(TradingAccount.id, TradingAccount.starting_balance)).all())
    if not rows:
        return settings.starting_balance
    if settings.default_account_id in rows:
        rows[settings.default_account_id] = settings.starting_balance
    if account_ids is None:
        return round(sum(rows.values()), 2)
    return round(sum(value for key, value in rows.items() if key in account_ids), 2)


def filter_trades(stmt, account_ids: tuple[int, ...] | None):
    """Add the account filter of a scope to a statement over ``Trade``."""
    return stmt if account_ids is None else stmt.where(Trade.account_id.in_(account_ids))


# --------------------------------------------------------------------------- live metrics


def _snapshot(account: TradingAccount, now: datetime) -> dict | None:
    """The bridge-reported figures when they are recent enough to count as live."""
    if not account.snapshot or account.snapshot_at is None or now - account.snapshot_at > SNAPSHOT_FRESH:
        return None
    return account.snapshot


def _margin(trade: Trade, market: Market | None, account_currency: str, leverage: int) -> float | None:
    """Estimated margin of an open position: notional in the account currency / leverage (None = unknown)."""
    lots = trade.remaining_size if trade.remaining_size is not None else trade.position_size
    if not lots or market is None or leverage <= 0:
        return None
    price = trade.last_price or trade.entry_price
    if market.quote_currency == account_currency:
        notional = lots * market.contract_size * price
    elif market.base_currency == account_currency:
        notional = lots * market.contract_size
    else:
        return None
    return notional / leverage


def account_metrics(db: Session, settings: AccountSettings, account: TradingAccount, now: datetime) -> dict:
    """Balance, equity, floating P&L and margin of one account: from its bridge when live, else the journal."""
    from . import account as account_service  # local import: account imports this module
    from .positions import unrealized_pnl

    ids = (account.id,)
    start = starting_balance(db, settings, ids)
    balance = round(start + account_service.realized_pnl(db, account_ids=ids), 2)
    open_trades = list(db.scalars(select(Trade).where(Trade.status == "open", Trade.account_id == account.id)))
    markets = {m.symbol: m for m in db.scalars(select(Market))}
    floating, priced, margin, margin_known = 0.0, 0, 0.0, 0
    for trade in open_trades:
        market = markets.get(trade.symbol)
        value = unrealized_pnl(trade, market, settings)
        if value is not None:
            floating += value
            priced += 1
        needed = _margin(trade, market, account.currency, account.leverage)
        if needed is not None:
            margin += needed
            margin_known += 1
    metrics = {
        "source": "journal",
        "balance": balance,
        "floating_pnl": round(floating, 2),
        "equity": round(balance + floating, 2),
        "margin": round(margin, 2) if open_trades else 0.0,
        "free_margin": None,
        "margin_level": None,
        "open_positions": len(open_trades),
        "priced_positions": priced,
        "margin_estimated": margin_known < len(open_trades),
        "updated_at": now.isoformat(),
    }
    metrics["free_margin"] = round(metrics["equity"] - metrics["margin"], 2)
    if metrics["margin"] > 0:
        metrics["margin_level"] = round(metrics["equity"] / metrics["margin"] * 100, 1)
    live = _snapshot(account, now)
    if live is not None:
        for key in ("balance", "equity", "floating_pnl", "margin", "free_margin", "margin_level"):
            if isinstance(live.get(key), (int, float)):
                metrics[key] = round(float(live[key]), 2)
        if isinstance(live.get("open_positions"), int):
            metrics["open_positions"] = live["open_positions"]
        metrics.update(source="bridge", margin_estimated=False, updated_at=account.snapshot_at.isoformat())
    metrics["change"] = round(metrics["equity"] - start, 2)
    return metrics


def account_out(db: Session, settings: AccountSettings, account: TradingAccount, metrics: dict | None = None) -> dict:
    """An account for the browser: never a secret, only which secrets are set (with a short hint)."""
    from . import secrets_store

    default = is_default(settings, account)
    challenge = db.scalar(select(PropChallenge.id).where(PropChallenge.account_id == account.id).limit(1))
    trades = db.scalar(select(func.count(Trade.id)).where(Trade.account_id == account.id)) or 0
    return {
        "id": account.id,
        "name": settings.account_name if default else account.name,
        "kind": account.kind,
        "broker": account.broker,
        "platform": account.platform,
        "login": account.login,
        "server": account.server,
        "currency": settings.currency if default else account.currency,
        "starting_balance": settings.starting_balance if default else account.starting_balance,
        "leverage": account.leverage,
        "color": account.color,
        "status": account.status,
        "notes": account.notes,
        "connection": account.connection,
        "connection_config": dict(account.connection_config or {}),
        "execution_enabled": account.execution_enabled,
        "max_lots_per_order": account.max_lots_per_order,
        "last_seen_at": account.last_seen_at.isoformat() if account.last_seen_at else None,
        "last_error": account.last_error,
        "credentials": secrets_store.described(db, account.id),
        "is_default": default,
        "is_active": settings.active_account_id == account.id,
        "prop_challenge_id": challenge,
        "trades": int(trades),
        "metrics": metrics,
    }


def summary(db: Session, settings: AccountSettings, scope: Scope, now: datetime) -> dict:
    """The dashboard header: the scope's totals plus every account's own figures."""
    accounts = list(db.scalars(select(TradingAccount).order_by(TradingAccount.id)))
    per_account = {a.id: account_metrics(db, settings, a, now) for a in accounts}
    counted = scope.accounts if scope.accounts else tuple(accounts)
    keys = ("balance", "equity", "floating_pnl", "margin", "free_margin", "change")
    total = {key: round(sum(per_account[a.id][key] or 0.0 for a in counted), 2) for key in keys}
    total["open_positions"] = sum(per_account[a.id]["open_positions"] for a in counted)
    total["margin_level"] = round(total["equity"] / total["margin"] * 100, 1) if total["margin"] > 0 else None
    total["live"] = any(per_account[a.id]["source"] == "bridge" for a in counted)
    total["margin_estimated"] = any(per_account[a.id]["margin_estimated"] for a in counted)
    return {
        "scope": {
            "account_id": scope.account.id if scope.account else None,
            "name": account_out(db, settings, scope.account)["name"] if scope.account else tr("All accounts"),
            "currency": (
                (scope.account.currency if scope.account else settings.currency)
                if not (scope.account and is_default(settings, scope.account))
                else settings.currency
            ),
            "accounts": [a.id for a in counted],
            "excluded": [a.id for a in scope.excluded],
        },
        "totals": total,
        "accounts": [account_out(db, settings, a, per_account[a.id]) for a in accounts],
        "updated_at": now.isoformat(),
    }
