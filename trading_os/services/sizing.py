"""Position sizing and trade-geometry math (pure functions, no I/O).

Conventions
-----------
* A *pip* (or index *point*) is ``spec.pip_size`` units of price.
* Money values are expressed in the account currency. Converting from the
  instrument's quote currency uses, in order of preference: an explicit
  ``fx_rate`` (1 quote unit = ``fx_rate`` account units), 1.0 when the quote
  currency *is* the account currency, or ``1 / price`` when the base currency
  is the account currency (e.g. USDJPY on a USD account).
* Lot sizes are always rounded **down** to the broker's lot step so the real
  risk never exceeds the planned risk.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import ROUND_DOWN, Decimal
from typing import Protocol

from .i18n import tr


class ContractSpec(Protocol):
    symbol: str
    base_currency: str | None
    quote_currency: str
    contract_size: float
    pip_size: float
    min_lot: float
    lot_step: float
    max_lot: float


class SizingError(ValueError):
    """Raised when a position size cannot be computed from the given inputs."""


def direction_from_stop(entry: float, stop: float) -> str:
    return "long" if stop < entry else "short"


def planned_rr(entry: float, stop: float, take_profit: float | None) -> float | None:
    """Reward-to-risk of the plan, e.g. ``2.0`` for a 1:2 trade."""
    risk = abs(entry - stop)
    if take_profit is None or risk == 0:
        return None
    return abs(take_profit - entry) / risk


def blended_rr(entry: float, stop: float, targets: list[tuple[float, float]]) -> float | None:
    """Reward-to-risk of a scale-out plan: targets weighted by the % closed at each.

    ``targets`` is ``[(price, close_pct), ...]`` with percentages summing to ~100.
    E.g. 50% at 1R + 50% at 3R is a 1:2 plan.
    """
    risk = abs(entry - stop)
    total = sum(pct for _, pct in targets)
    if not targets or risk == 0 or total <= 0:
        return None
    return sum(abs(price - entry) * pct for price, pct in targets) / total / risk


def executed_rr(direction: str, entry: float, stop: float, exit_price: float | None) -> float | None:
    """Realised move in units of initial risk (price based, before costs)."""
    risk = abs(entry - stop)
    if exit_price is None or risk == 0:
        return None
    sign = 1.0 if direction == "long" else -1.0
    return sign * (exit_price - entry) / risk


def quote_to_account_rate(
    spec: ContractSpec,
    account_currency: str,
    price: float | None = None,
    fx_rate: float | None = None,
) -> float | None:
    """Value of one quote-currency unit in the account currency, if derivable."""
    if fx_rate:
        return fx_rate
    if spec.quote_currency == account_currency:
        return 1.0
    if spec.base_currency == account_currency and price:
        return 1.0 / price
    return None


def conversion_hint(spec: ContractSpec, account_currency: str) -> str:
    return tr(
        "{symbol} is quoted in {quote}; provide the {quote}->{account} conversion rate",
        symbol=spec.symbol,
        quote=spec.quote_currency,
        account=account_currency,
    )


def gross_pnl(
    spec: ContractSpec,
    account_currency: str,
    direction: str,
    entry: float,
    exit_price: float,
    lots: float,
) -> float | None:
    """Gross P&L of a closed position in account currency (``None`` if not convertible)."""
    rate = quote_to_account_rate(spec, account_currency, price=exit_price)
    if rate is None:
        return None
    sign = 1.0 if direction == "long" else -1.0
    return sign * (exit_price - entry) * spec.contract_size * lots * rate


def risk_for_lots(
    spec: ContractSpec,
    account_currency: str,
    entry: float,
    stop: float,
    lots: float,
) -> float | None:
    """Money lost if ``lots`` are stopped out at ``stop``."""
    rate = quote_to_account_rate(spec, account_currency, price=entry)
    if rate is None:
        return None
    return abs(entry - stop) * spec.contract_size * lots * rate


def round_lots(raw_lots: float, spec: ContractSpec) -> float:
    """Floor ``raw_lots`` to the lot step and cap at max lot; 0 if below min lot."""
    step = Decimal(str(spec.lot_step))
    # Strip float noise first (150.00 - 149.70 = 0.29999999999998) so it can't
    # push an exact size like 0.5 down a whole step.
    raw = Decimal(str(round(raw_lots, 9)))
    lots = (raw / step).to_integral_value(rounding=ROUND_DOWN) * step
    lots = min(lots, Decimal(str(spec.max_lot)))
    if lots < Decimal(str(spec.min_lot)):
        return 0.0
    return float(lots)


@dataclass
class SizingResult:
    symbol: str
    currency: str
    direction: str | None
    account_equity: float
    risk_pct: float
    risk_amount: float
    stop_pips: float
    stop_distance: float
    pip_value_per_lot: float
    fx_rate: float
    raw_lots: float
    lots: float
    units: float
    max_loss: float
    actual_risk_pct: float
    target_pips: float | None = None
    rr: float | None = None
    potential_profit: float | None = None
    notional: float | None = None
    leverage: float | None = None
    notes: list[str] = field(default_factory=list)


def calculate_position_size(
    spec: ContractSpec,
    account_currency: str,
    equity: float,
    risk_pct: float,
    *,
    entry: float | None = None,
    stop: float | None = None,
    stop_pips: float | None = None,
    take_profit: float | None = None,
    target_pips: float | None = None,
    fx_rate: float | None = None,
) -> SizingResult:
    """Size a position so that hitting the stop loses ``risk_pct`` of ``equity``.

    The stop is taken from ``entry``/``stop`` prices when both are given,
    otherwise from ``stop_pips``. The target (optional) is taken from
    ``take_profit`` or ``target_pips`` and only affects the R:R outputs.
    """
    if equity <= 0 or risk_pct <= 0:
        raise SizingError(tr("Equity and risk % must be positive"))

    direction = None
    if entry is not None and stop is not None:
        if entry == stop:
            raise SizingError(tr("Stop price must differ from entry price"))
        stop_distance = abs(entry - stop)
        direction = direction_from_stop(entry, stop)
    elif stop_pips:
        stop_distance = stop_pips * spec.pip_size
    else:
        raise SizingError(tr("Provide entry + stop prices or a stop size in pips/points"))

    rate = quote_to_account_rate(spec, account_currency, price=entry, fx_rate=fx_rate)
    if rate is None:
        raise SizingError(tr("{hint} or an entry price", hint=conversion_hint(spec, account_currency)))

    pips = stop_distance / spec.pip_size
    pip_value = spec.contract_size * spec.pip_size * rate
    risk_amount = equity * risk_pct / 100.0
    raw_lots = risk_amount / (pips * pip_value)
    lots = round_lots(raw_lots, spec)

    notes: list[str] = []
    if lots == 0.0:
        min_risk = spec.min_lot * pips * pip_value
        notes.append(
            tr(
                "Stop too wide for this risk: the minimum lot ({min_lot:g}) would risk "
                "{min_risk:,.2f} {currency} ({min_risk_pct:.2f}% of equity).",
                min_lot=spec.min_lot,
                min_risk=min_risk,
                currency=account_currency,
                min_risk_pct=min_risk / equity * 100,
            )
        )
    elif lots >= spec.max_lot and raw_lots > spec.max_lot:
        notes.append(tr("Capped at the broker maximum of {max_lot:g} lots.", max_lot=spec.max_lot))
    elif abs(lots - raw_lots) > 1e-9:
        notes.append(
            tr(
                "Rounded down from {raw_lots:.4f} to {lots:g} lots (lot step {step:g}).",
                raw_lots=raw_lots,
                lots=lots,
                step=spec.lot_step,
            )
        )

    max_loss = lots * pips * pip_value

    result = SizingResult(
        symbol=spec.symbol,
        currency=account_currency,
        direction=direction,
        account_equity=equity,
        risk_pct=risk_pct,
        risk_amount=risk_amount,
        stop_pips=pips,
        stop_distance=stop_distance,
        pip_value_per_lot=pip_value,
        fx_rate=rate,
        raw_lots=raw_lots,
        lots=lots,
        units=lots * spec.contract_size,
        max_loss=max_loss,
        actual_risk_pct=max_loss / equity * 100.0,
        notes=notes,
    )

    if take_profit is not None and entry is not None:
        if direction and (take_profit - entry) * (1 if direction == "long" else -1) <= 0:
            raise SizingError(tr("Take profit is on the wrong side of the entry for this stop"))
        target_pips = abs(take_profit - entry) / spec.pip_size
    if target_pips:
        result.target_pips = target_pips
        result.rr = target_pips / pips
        result.potential_profit = lots * target_pips * pip_value

    if entry is not None:
        result.notional = lots * spec.contract_size * entry * rate
        result.leverage = result.notional / equity

    return result
