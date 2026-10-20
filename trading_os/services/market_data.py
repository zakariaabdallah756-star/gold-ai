"""Market data for the strategy alert engine: closed OHLC candles from a provider.

``fetch_candles`` returns CLOSED bars only (oldest first): the still-forming
last bar is dropped. ``Bar.time`` is the bar's OPEN time as a naive datetime in
the account time zone.

Providers:

* ``demo`` - deterministic synthetic candles (works offline). Not real prices.
* ``yahoo`` - Yahoo Finance chart API (no key; unofficial, may be delayed or
  rate limited). H4 is aggregated from 60-minute bars.
* ``binance`` - Binance public klines (crypto only).
* ``twelvedata`` - Twelve Data time series (API key in
  ``AccountSettings.market_api_keys["twelvedata"]``, sent as a header).
* ``push`` - candles pushed by the user's platform into the ``Candle`` table
  (``POST /api/alerts/candles``).

Every outbound request goes through ``_http_get`` (tests monkeypatch it). A
small in-memory cache keeps one scan from fetching the same series twice and
avoids refetching a series before its next bar can have closed.
"""

from __future__ import annotations

import hashlib
import math
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import __version__
from ..models import AccountSettings, Candle, Market

TIMEFRAMES = ("M1", "M5", "M15", "M30", "H1", "H4", "D1")
TF_MINUTES = {"M1": 1, "M5": 5, "M15": 15, "M30": 30, "H1": 60, "H4": 240, "D1": 1440}
MAX_BARS = 500
HTTP_TIMEOUT = 8.0
USER_AGENT = f"TradingOS/{__version__}"
MIN_REFETCH = timedelta(seconds=60)
CACHE_LIMIT = 256


class MarketDataError(Exception):
    """Candles could not be loaded; the message is shown to the user as is."""


@dataclass(frozen=True)
class Bar:
    time: datetime  # bar OPEN time, naive account-local
    open: float
    high: float
    low: float
    close: float
    volume: float | None = None


@dataclass(frozen=True)
class Provider:
    key: str
    label: str
    needs_key: bool
    description: str
    symbol_map: dict[str, str]


YAHOO_SYMBOLS = {
    "XAUUSD": "GC=F",
    "XAGUSD": "SI=F",
    "EURUSD": "EURUSD=X",
    "GBPUSD": "GBPUSD=X",
    "USDJPY": "JPY=X",
    "AUDUSD": "AUDUSD=X",
    "USDCAD": "CAD=X",
    "NAS100": "^NDX",
    "US30": "^DJI",
    "US500": "^GSPC",
    "SPX500": "^GSPC",
    "GER40": "^GDAXI",
    "BTCUSD": "BTC-USD",
    "ETHUSD": "ETH-USD",
}
BINANCE_SYMBOLS = {
    "BTCUSD": "BTCUSDT",
    "ETHUSD": "ETHUSDT",
    "SOLUSD": "SOLUSDT",
    "XRPUSD": "XRPUSDT",
    "BNBUSD": "BNBUSDT",
    "ADAUSD": "ADAUSDT",
    "DOGEUSD": "DOGEUSDT",
    "LTCUSD": "LTCUSDT",
}
TWELVEDATA_SYMBOLS = {
    "XAUUSD": "XAU/USD",
    "XAGUSD": "XAG/USD",
    "EURUSD": "EUR/USD",
    "GBPUSD": "GBP/USD",
    "USDJPY": "USD/JPY",
    "AUDUSD": "AUD/USD",
    "USDCAD": "USD/CAD",
    "BTCUSD": "BTC/USD",
    "ETHUSD": "ETH/USD",
}

PROVIDERS: dict[str, Provider] = {
    p.key: p
    for p in (
        Provider(
            "demo",
            "Demo data (synthetic) - not real prices",
            False,
            "Generated candles for trying the scanner offline. Never trade from them.",
            {},
        ),
        Provider(
            "yahoo",
            "Yahoo Finance",
            False,
            "Free, no key. Unofficial: data may be delayed or rate limited. Gold uses COMEX gold futures (GC=F), "
            "not spot, so levels differ slightly from your broker.",
            YAHOO_SYMBOLS,
        ),
        Provider("binance", "Binance (crypto)", False, "Free public crypto candles (USDT pairs).", BINANCE_SYMBOLS),
        Provider(
            "twelvedata",
            "Twelve Data",
            True,
            "Forex, metals and crypto with your Twelve Data API key (free tier available, rate limited).",
            TWELVEDATA_SYMBOLS,
        ),
        Provider(
            "push",
            "Pushed from my platform",
            False,
            "Candles sent by your MT4/MT5 script, TradingView webhook or own script to POST /api/alerts/candles.",
            {},
        ),
    )
}
PROVIDER_KEYS = tuple(PROVIDERS)

# Typical price and pip size per symbol for demo candles (plan markets refine the pip size).
DEMO_PRICES = {
    "XAUUSD": 2650.0,
    "XAGUSD": 31.0,
    "EURUSD": 1.085,
    "GBPUSD": 1.27,
    "USDJPY": 150.0,
    "AUDUSD": 0.66,
    "USDCAD": 1.37,
    "NZDUSD": 0.6,
    "USDCHF": 0.88,
    "US30": 42000.0,
    "NAS100": 20000.0,
    "US500": 5800.0,
    "SPX500": 5800.0,
    "GER40": 19000.0,
    "BTCUSD": 65000.0,
    "ETHUSD": 2600.0,
}
DEMO_DAILY_VOL = 0.012  # ~1.2% per day
DEMO_MEMORY = 256  # bars in the moving sum that shapes the demo walk


def timeframe_delta(timeframe: str) -> timedelta:
    return timedelta(minutes=TF_MINUTES[timeframe])


def provider_symbol(provider: str, symbol: str, overrides: dict | None = None) -> str:
    """The provider's ticker for a journal symbol (rule override, then default mapping)."""
    override = (overrides or {}).get(symbol)
    if override:
        return override
    mapped = PROVIDERS[provider].symbol_map.get(symbol)
    if mapped:
        return mapped
    if provider == "binance" and symbol.endswith("USD"):
        return symbol + "T"
    if provider == "twelvedata" and len(symbol) == 6 and symbol.isalpha():
        return f"{symbol[:3]}/{symbol[3:]}"
    return symbol


def mask_secret(value: str) -> str:
    """``••••abcd`` for a configured secret, '' when empty."""
    return f"••••{value[-4:]}" if value and len(value) > 8 else ("••••" if value else "")


# --------------------------------------------------------------------------- HTTP


def _http_get(url: str, params: dict[str, Any], headers: dict[str, str] | None = None) -> tuple[int, Any]:
    """GET ``url``; returns ``(status code, decoded JSON or None)``. Network failures raise httpx errors."""
    request_headers = {"User-Agent": USER_AGENT, "Accept": "application/json", **(headers or {})}
    with httpx.Client(timeout=HTTP_TIMEOUT, follow_redirects=False, headers=request_headers) as client:
        response = client.get(url, params=params)
    try:
        payload = response.json()
    except ValueError:
        payload = None
    return response.status_code, payload


def _get_json(label: str, url: str, params: dict[str, Any], headers: dict[str, str] | None = None) -> tuple[int, Any]:
    try:
        status, payload = _http_get(url, params, headers)
    except httpx.TimeoutException:
        raise MarketDataError(f"{label} did not answer in time - try again later.") from None
    except httpx.HTTPError:
        raise MarketDataError(f"{label} did not answer - check your internet connection.") from None
    if status == 429:
        raise MarketDataError(f"{label} is rate limiting requests - use a longer scan interval.")
    return status, payload


def _to_local(epoch_seconds: float, tz: ZoneInfo) -> datetime:
    return datetime.fromtimestamp(epoch_seconds, timezone.utc).astimezone(tz).replace(tzinfo=None)


def _num(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _make_bar(time: datetime, o: Any, h: Any, low: Any, c: Any, v: Any = None) -> Bar | None:
    values = [_num(o), _num(h), _num(low), _num(c)]
    if any(value is None or value <= 0 for value in values):
        return None
    open_, high, low_, close = values
    return Bar(time, open_, max(high, open_, close), min(low_, open_, close), close, _num(v))


# --------------------------------------------------------------------------- providers

_YAHOO_INTERVALS = {
    "M1": ("1m", "5d"),
    "M5": ("5m", "5d"),
    "M15": ("15m", "1mo"),
    "M30": ("30m", "1mo"),
    "H1": ("60m", "3mo"),
    "H4": ("60m", "1y"),
    "D1": ("1d", "2y"),
}


def _yahoo(ticker: str, timeframe: str, tz: ZoneInfo, settings: AccountSettings) -> list[Bar]:
    label = "Yahoo Finance"
    interval, range_ = _YAHOO_INTERVALS[timeframe]
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}"
    status, payload = _get_json(label, url, {"interval": interval, "range": range_, "includePrePost": "false"})
    chart = payload.get("chart") if isinstance(payload, dict) else None
    chart = chart if isinstance(chart, dict) else {}
    error = chart.get("error") if isinstance(chart.get("error"), dict) else {}
    if status == 404 or "not found" in str(error.get("code", "")).lower():
        raise MarketDataError(f"Unknown symbol {ticker} on {label} - set the provider symbol for this market.")
    results = chart.get("result")
    if status != 200 or not results:
        detail = str(error.get("description") or "")[:120]
        raise MarketDataError(
            f"{label} returned no data for {ticker}" + (f": {detail}" if detail else f" (HTTP {status}).")
        )
    result = results[0]
    stamps = result.get("timestamp") or []
    quote = ((result.get("indicators") or {}).get("quote") or [{}])[0]
    opens, highs, lows, closes = (quote.get(k) or [] for k in ("open", "high", "low", "close"))
    volumes = quote.get("volume") or []
    bars: list[tuple[float, Bar]] = []
    for index, stamp in enumerate(stamps):
        try:
            row = (opens[index], highs[index], lows[index], closes[index])
        except IndexError:
            break
        bar = _make_bar(_to_local(stamp, tz), *row, volumes[index] if index < len(volumes) else None)
        if bar is not None:
            bars.append((float(stamp), bar))
    if not bars:
        raise MarketDataError(f"{label} returned no candles for {ticker}.")
    if timeframe == "H4":
        return aggregate(bars, 4 * 3600, tz)
    return [bar for _, bar in bars]


def aggregate(stamped: list[tuple[float, Bar]], seconds: int, tz: ZoneInfo) -> list[Bar]:
    """Merge ``(epoch, bar)`` pairs into buckets of ``seconds`` aligned to UTC midnight."""
    out: list[Bar] = []
    current_key: int | None = None
    o = hi = lo = c = 0.0
    volume: float | None = None
    for stamp, bar in stamped:
        key = int(stamp // seconds) * seconds
        if key != current_key:
            if current_key is not None:
                out.append(Bar(_to_local(current_key, tz), o, hi, lo, c, volume))
            current_key, o, hi, lo, c, volume = key, bar.open, bar.high, bar.low, bar.close, bar.volume
        else:
            hi, lo, c = max(hi, bar.high), min(lo, bar.low), bar.close
            if bar.volume is not None:
                volume = (volume or 0.0) + bar.volume
    if current_key is not None:
        out.append(Bar(_to_local(current_key, tz), o, hi, lo, c, volume))
    return out


_BINANCE_INTERVALS = {"M1": "1m", "M5": "5m", "M15": "15m", "M30": "30m", "H1": "1h", "H4": "4h", "D1": "1d"}


def _binance(ticker: str, timeframe: str, tz: ZoneInfo, settings: AccountSettings) -> list[Bar]:
    label = "Binance"
    params = {"symbol": ticker, "interval": _BINANCE_INTERVALS[timeframe], "limit": MAX_BARS}
    status, payload = _get_json(label, "https://api.binance.com/api/v3/klines", params)
    if status == 400 and isinstance(payload, dict) and "symbol" in str(payload.get("msg", "")).lower():
        raise MarketDataError(f"Unknown symbol {ticker} on {label} - use a USDT pair such as BTCUSDT.")
    if status == 451:
        raise MarketDataError(f"{label} is not available from your region - choose another provider.")
    if status != 200 or not isinstance(payload, list):
        message = payload.get("msg") if isinstance(payload, dict) else ""
        raise MarketDataError(
            f"{label} returned an error for {ticker}" + (f": {message}" if message else f" (HTTP {status}).")
        )
    bars = []
    for row in payload:
        if isinstance(row, list) and len(row) >= 6:
            bar = _make_bar(_to_local(row[0] / 1000, tz), row[1], row[2], row[3], row[4], row[5])
            if bar is not None:
                bars.append(bar)
    return bars


_TWELVEDATA_INTERVALS = {
    "M1": "1min",
    "M5": "5min",
    "M15": "15min",
    "M30": "30min",
    "H1": "1h",
    "H4": "4h",
    "D1": "1day",
}


def _twelvedata(ticker: str, timeframe: str, tz: ZoneInfo, settings: AccountSettings) -> list[Bar]:
    label = "Twelve Data"
    key = (settings.market_api_keys or {}).get("twelvedata", "")
    if not key:
        raise MarketDataError("Twelve Data API key missing - add it under Strategy alerts → Data providers.")
    params = {
        "symbol": ticker,
        "interval": _TWELVEDATA_INTERVALS[timeframe],
        "outputsize": MAX_BARS,
        "timezone": "UTC",
        "order": "asc",
    }
    status, payload = _get_json(
        label, "https://api.twelvedata.com/time_series", params, {"Authorization": f"apikey {key}"}
    )
    if not isinstance(payload, dict):
        raise MarketDataError(f"{label} returned an unreadable answer (HTTP {status}).")
    if payload.get("status") == "error" or status != 200:
        code = payload.get("code")
        message = str(payload.get("message", ""))
        if code == 401 or "apikey" in message.lower():
            raise MarketDataError("Twelve Data rejected the API key - check it under Data providers.")
        if code == 429:
            raise MarketDataError(f"{label} is rate limiting requests - use a longer scan interval.")
        if code in (400, 404) and "symbol" in message.lower():
            raise MarketDataError(f"Unknown symbol {ticker} on {label} - set the provider symbol for this market.")
        raise MarketDataError(f"{label} returned an error for {ticker}: {message[:120] or f'HTTP {status}'}")
    bars = []
    for row in payload.get("values") or []:
        raw = str(row.get("datetime", ""))
        try:
            moment = datetime.fromisoformat(raw)
        except ValueError:
            continue
        local = moment.replace(tzinfo=timezone.utc).astimezone(tz).replace(tzinfo=None)
        bar = _make_bar(local, row.get("open"), row.get("high"), row.get("low"), row.get("close"), row.get("volume"))
        if bar is not None:
            bars.append(bar)
    bars.sort(key=lambda bar: bar.time)
    return bars


def _push(db: Session, ticker: str, timeframe: str) -> list[Bar]:
    rows = db.scalars(
        select(Candle)
        .where(Candle.symbol == ticker, Candle.timeframe == timeframe)
        .order_by(Candle.time.desc())
        .limit(MAX_BARS + 1)
    ).all()
    if not rows:
        raise MarketDataError(
            f"No candles pushed yet for {ticker} {timeframe} - send them to POST /api/alerts/candles."
        )
    return [Bar(r.time, r.open, r.high, r.low, r.close, r.volume) for r in reversed(rows)]


def _unit_pair(*parts: object) -> tuple[float, float]:
    digest = hashlib.blake2b("|".join(map(str, parts)).encode(), digest_size=8).digest()
    a = int.from_bytes(digest[:4], "big") / 2**32
    b = int.from_bytes(digest[4:], "big") / 2**32
    return a, b


def _gauss(*parts: object) -> float:
    u1, u2 = _unit_pair(*parts)
    return math.sqrt(-2.0 * math.log(max(u1, 1e-12))) * math.cos(2 * math.pi * u2)


def demo_scale(db: Session, symbol: str) -> tuple[float, float]:
    """Typical price and pip size for demo candles of ``symbol``."""
    market = db.scalar(select(Market).where(Market.symbol == symbol))
    pip = market.pip_size if market else (0.01 if symbol.endswith("JPY") else 0.0001)
    price = DEMO_PRICES.get(symbol) or pip * 10_000
    return price, pip


def demo_bars(
    symbol: str, timeframe: str, now: datetime, tz: ZoneInfo, price: float, pip: float, count: int
) -> list[Bar]:
    """Deterministic synthetic candles ending with the last bar closed before ``now`` (account-local).

    Each bar's move is seeded by (symbol, timeframe, bar time), and the log price
    is a moving sum of the last ``DEMO_MEMORY`` moves - so a given bar always has
    the same OHLC whatever window is requested, and prices stay near ``price``.
    """
    step = TF_MINUTES[timeframe] * 60
    now_epoch = now.replace(tzinfo=tz).timestamp()
    last_open = int(now_epoch // step) * step - step  # newest fully closed bar
    first = last_open - (count - 1) * step
    sigma = DEMO_DAILY_VOL * math.sqrt(TF_MINUTES[timeframe] / 1440)
    decimals = max(0, -math.floor(math.log10(pip)) + 1) if pip > 0 else 2

    def move(epoch: int) -> float:
        return _gauss(symbol, timeframe, epoch) * sigma

    window = [move(first - (DEMO_MEMORY - k) * step) for k in range(DEMO_MEMORY)]
    total = sum(window)
    prev_close = round(price * math.exp(total), decimals)
    bars: list[Bar] = []
    for k in range(count):
        epoch = first + k * step
        current = move(epoch)
        total += current - window[k % DEMO_MEMORY]
        window[k % DEMO_MEMORY] = current
        close = round(price * math.exp(total), decimals)
        u1, u2 = _unit_pair(symbol, timeframe, epoch, "wick")
        open_ = prev_close
        high = round(max(open_, close) + u1 * sigma * price * 0.8, decimals)
        low = round(min(open_, close) - u2 * sigma * price * 0.8, decimals)
        volume = float(500 + int(u1 * 1500))
        bars.append(Bar(_to_local(epoch, tz), open_, high, low, close, volume))
        prev_close = close
    return bars


# --------------------------------------------------------------------------- public API


@dataclass
class _CacheEntry:
    bars: list[Bar]
    valid_until: datetime


_cache: dict[tuple[str, str, str, str], _CacheEntry] = {}
_cache_lock = threading.Lock()


def clear_cache() -> None:
    with _cache_lock:
        _cache.clear()


def closed_only(bars: list[Bar], timeframe: str, now: datetime) -> list[Bar]:
    """Drop bars that have not closed yet at ``now`` (the still-forming bar)."""
    delta = timeframe_delta(timeframe)
    return [bar for bar in bars if bar.time + delta <= now]


_REMOTE = {"yahoo": _yahoo, "binance": _binance, "twelvedata": _twelvedata}


def fetch_candles(
    db: Session,
    settings: AccountSettings,
    provider: str,
    symbol: str,
    timeframe: str,
    *,
    provider_symbols: dict | None = None,
    now: datetime,
    limit: int = MAX_BARS,
) -> list[Bar]:
    """Closed candles for ``symbol`` (journal symbol), oldest first, at most ``limit``.

    ``now`` is the account-local wall clock. Raises ``MarketDataError`` with a
    readable message when the provider fails or returns nothing.
    """
    if provider not in PROVIDERS:
        raise MarketDataError(f"Unknown market data provider '{provider}'.")
    if timeframe not in TF_MINUTES:
        raise MarketDataError(f"Unsupported timeframe '{timeframe}'.")
    tz = ZoneInfo(settings.timezone)
    ticker = provider_symbol(provider, symbol, provider_symbols)

    if provider == "demo":
        price, pip = demo_scale(db, symbol)
        return demo_bars(symbol, timeframe, now, tz, price, pip, limit)
    if provider == "push":
        bars = closed_only(_push(db, ticker, timeframe), timeframe, now)
    else:
        key = (provider, ticker, timeframe, settings.timezone)
        with _cache_lock:
            entry = _cache.get(key)
        if entry is not None and now < entry.valid_until:
            bars = entry.bars
        else:
            bars = closed_only(_REMOTE[provider](ticker, timeframe, tz, settings), timeframe, now)
            if not bars:
                raise MarketDataError(f"No closed {timeframe} candles for {ticker} yet.")
            # The next bar closes one period after the newest closed bar; retry at most once a minute meanwhile.
            next_close = bars[-1].time + 2 * timeframe_delta(timeframe)
            with _cache_lock:
                if len(_cache) >= CACHE_LIMIT:
                    _cache.clear()
                _cache[key] = _CacheEntry(bars, max(next_close, now + MIN_REFETCH))
    if not bars:
        raise MarketDataError(f"No closed {timeframe} candles for {ticker} yet.")
    return bars[-limit:]
