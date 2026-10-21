"""Parse free-text trade signals (Telegram / WhatsApp style) into structured fields.

Pure functions, no I/O. :func:`parse_signal` never raises on bad input: it
returns whatever it recognised plus human-readable ``warnings``. Fields that
are missing from the message are ``None`` (or empty lists).

Pipeline
--------
1. Normalise unicode (NFKC, dashes/minus signs, exotic spaces).
2. Find the symbol (aliases, broker suffixes, FX pairs, configured symbols)
   and blank it out, so digits inside tickers (NAS100, US30) are not prices.
3. Split the text at labels (``SL``, ``TP1``, ``Entry``, ``@``, ``BUY`` ...):
   each label owns the numbers up to the next label.
4. Read numbers (thousands separators, decimal commas, ``67 250``) and settle
   ambiguous ones (``18,550`` vs ``1,085``) against the scale of the others.
5. Assemble entry / zone, stop and targets, then sanity-check the geometry.

The module also holds the pure helpers that turn a signal into a scale-out
ticket (:func:`resolve_close_pcts`, :func:`split_lots`).
"""

from __future__ import annotations

import math
import re
import statistics
import unicodedata
from collections.abc import Iterable
from dataclasses import asdict, dataclass, field
from decimal import ROUND_DOWN, Decimal

from ..constants import DEFAULT_TP_SPLITS, MAX_TARGETS


@dataclass
class ParsedSignal:
    symbol: str | None = None  # canonical / configured symbol, e.g. XAUUSD
    symbol_raw: str | None = None  # as written in the message, e.g. "#XAUUSDm"
    direction: str | None = None  # long | short
    order_type: str = "market"  # market | limit | stop
    entry: float | None = None
    entry_low: float | None = None
    entry_high: float | None = None
    stop_loss: float | None = None
    stop_pips: float | None = None  # relative stop ("SL 30 pips")
    take_profits: list[float] = field(default_factory=list)  # TP1..TP4, nearest first
    target_pips: list[float] = field(default_factory=list)  # relative targets ("TP 50 pips")
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


# --------------------------------------------------------------------------- normalisation

_DASHES = dict.fromkeys(map(ord, "‐‑‒–—―−﹘﹣－"), "-")
_SPACES = dict.fromkeys(map(ord, "            "), " ")
_INVISIBLE = dict.fromkeys(map(ord, "​‌‍⁠︎️"), None)
_ASCII_LOWER = str.maketrans("ABCDEFGHIJKLMNOPQRSTUVWXYZ", "abcdefghijklmnopqrstuvwxyz")


def _normalise(text: str) -> str:
    text = unicodedata.normalize("NFKC", text)
    return text.translate(_DASHES).translate(_SPACES).translate(_INVISIBLE).replace("\t", " ")


def _fmt(value: float) -> str:
    return f"{value:g}" if abs(value) < 1e6 else f"{value:.0f}"


def _fmt_list(values: list, limit: int = 6) -> str:
    """``2680, 2690`` - at most ``limit`` items, so a garbage paste cannot flood the warnings."""
    items = [_fmt(v) if isinstance(v, float) else str(v) for v in values]
    return ", ".join(items[:limit]) + (f" and {len(items) - limit} more" if len(items) > limit else "")


# --------------------------------------------------------------------------- symbols

_CURRENCIES = frozenset("USD EUR GBP JPY CHF AUD CAD NZD SEK NOK DKK PLN HUF CZK TRY ZAR MXN SGD HKD CNH".split())
# Pairs of these currencies always trade below 1000 - used to read "1,085" as 1.085.
_LOW_PRICED = frozenset("EUR GBP CHF AUD CAD NZD USD JPY".split())
# Instruments quoted in the thousands - used to read "18.550" (European thousands dot) as 18550.
_HIGH_PRICED = frozenset("XAUUSD US30 NAS100 SPX500 GER40 BTCUSD ETHUSD".split())

_ALIASES = {
    **dict.fromkeys(("GOLD", "XAU", "XAUUSD", "GOLDUSD"), "XAUUSD"),
    **dict.fromkeys(("SILVER", "XAG", "XAGUSD"), "XAGUSD"),
    **dict.fromkeys(("DOW", "DJ30", "US30", "WS30", "DJI", "DOWJONES"), "US30"),
    **dict.fromkeys(("NASDAQ", "NASDAQ100", "NAS100", "USTEC", "NQ", "US100", "NDX"), "NAS100"),
    **dict.fromkeys(("SP500", "SPX", "SPX500", "US500"), "SPX500"),
    **dict.fromkeys(("DAX", "DAX40", "GER40", "DE40", "GER30", "DE30"), "GER40"),
    **dict.fromkeys(("BTC", "BITCOIN", "BTCUSD", "BTCUSDT", "XBTUSD"), "BTCUSD"),
    **dict.fromkeys(("ETH", "ETHEREUM", "ETHUSD", "ETHUSDT"), "ETHUSD"),
}
# Upper-case broker suffixes ("XAUUSDM", "EURUSDPRO"); only stripped when the rest is a known symbol.
_UPPER_SUFFIXES = ("MICRO", "MINI", "CASH", "PRO", "ECN", "RAW", "STD", "M", "C", "I", "Z", "X")
# Words written in capitals in signal messages that are never the instrument.
_NOT_SYMBOLS = frozenset("""
    BUY SELL LONG SHORT BULLISH BEARISH LIMIT STOP LOSS NOW MARKET ENTRY ENTER ZONE AREA PRICE BETWEEN AND OR
    TAKE PROFIT TARGET TARGETS TGT TP SL PIPS PIP POINTS PTS RISK REWARD LOT LOTS SIZE RR CMP INSTANT
    SIGNAL SIGNALS VIP FREE PREMIUM UPDATE ALERT NEW TRADE TRADES SETUP SCALP SWING INTRADAY HIT CLOSE CLOSED
    OPEN RUNNER ACTIVE RUNNING MOVE BE BREAKEVEN TSL TRAIL GMT UTC EST AM PM TF THE AT TO FOR WITH ON IN OF
    ONLY ABOVE BELOW USE MONEY MANAGEMENT DYOR NFA ORDER PENDING EXECUTE EXECUTED HOLD SURE SAFE
    """.split())

_PAIR_RE = re.compile(r"(?<![A-Za-z0-9])#?([A-Za-z]{3})\s?/\s?([A-Za-z]{3,4})(?![A-Za-z0-9])")
_TOKEN_RE = re.compile(r"(?<![A-Za-z0-9#])#?[A-Za-z][A-Za-z0-9]*(?:[._][A-Za-z0-9]{1,6}|[+!])?")
_LABEL_WORD_RE = re.compile(r"^(?:TP|SL|TGT|T|S)\d*$|^[MHDW]\d{1,2}$")
_KEYWORD_SUFFIXES = frozenset({"buy", "sell", "long", "short", "sl", "tp", "entry", "now", "limit", "stop", "target"})


def _is_fx_pair(code: str) -> bool:
    return len(code) == 6 and code[:3] in _CURRENCIES and code[3:] in _CURRENCIES and code[:3] != code[3:]


def _canonical(code: str) -> str | None:
    """Canonical symbol for an alias or FX pair (``None`` when unknown)."""
    if code in _ALIASES:
        return _ALIASES[code]
    return code if _is_fx_pair(code) else None


def _token_candidates(token: str) -> list[str]:
    """Upper-case spellings to try for a ticker token, broker suffixes removed."""
    core = re.split(r"[._+!]", token.lstrip("#"), maxsplit=1)[0]
    upper = core.upper()
    out = [upper]
    lower_suffix = re.fullmatch(r"([A-Z0-9]+?)[a-z]{1,4}", core)  # XAUUSDm, EURUSDpro, US30cash
    if lower_suffix:
        out.append(lower_suffix.group(1))
    out += [
        upper[: -len(suffix)] for suffix in _UPPER_SUFFIXES if upper.endswith(suffix) and len(upper) > len(suffix) + 2
    ]
    return out


@dataclass
class _SymbolHit:
    symbol: str
    raw: str
    configured: bool
    start: int
    end: int


class _SymbolBook:
    """Resolves tokens against aliases, FX pairs and the configured symbols."""

    def __init__(self, symbols: Iterable[str]):
        self.configured: dict[str, str] = {}
        self.by_canonical: dict[str, str] = {}
        for symbol in symbols:
            key = symbol.strip().upper()
            if key:
                self.configured.setdefault(key, symbol)
                # "XAUUSD.M" / "XAUUSDM" configured at the broker's spelling still answer to GOLD / XAUUSD.
                canonical = next(filter(None, map(_canonical, _token_candidates(key))), key)
                self.by_canonical.setdefault(canonical, symbol)

    def resolve(self, candidates: list[str]) -> tuple[str, bool] | None:
        for code in candidates:
            if code in self.configured:
                return self.configured[code], True
            canonical = _canonical(code)
            if canonical:
                return self.by_canonical.get(canonical, canonical), canonical in self.by_canonical
        return None


def _find_symbols(text: str, book: _SymbolBook) -> list[_SymbolHit]:
    hits: list[_SymbolHit] = []
    taken: list[tuple[int, int]] = []
    for match in _PAIR_RE.finditer(text):
        resolved = book.resolve([(match.group(1) + match.group(2)).upper()])
        if resolved:
            hits.append(_SymbolHit(resolved[0], match.group(0), resolved[1], match.start(), match.end()))
            taken.append(match.span())
    for match in _TOKEN_RE.finditer(text):
        if any(start <= match.start() < end for start, end in taken):
            continue
        token, end = match.group(0), match.end()
        suffix = re.search(r"[._]([A-Za-z0-9]+)$", token)
        if suffix and suffix.group(1).lower() in _KEYWORD_SUFFIXES:  # "gold.buy" is two words
            token, end = token[: suffix.start()], match.start() + suffix.start()
        resolved = book.resolve(_token_candidates(token))
        if resolved:
            hits.append(_SymbolHit(resolved[0], token, resolved[1], match.start(), end))
    return sorted(hits, key=lambda hit: hit.start)


def _unknown_symbol(text: str, hidden: list[tuple[int, int]]) -> _SymbolHit | None:
    """First ticker-looking token (``#TAG`` or ALL CAPS) that is not a keyword."""
    for match in _TOKEN_RE.finditer(text):
        if any(start <= match.start() < end for start, end in hidden):
            continue
        token = match.group(0)
        core = re.split(r"[._+!]", token.lstrip("#"), maxsplit=1)[0]
        letters = re.sub(r"[^A-Za-z]", "", core)
        upper = core.upper()
        if len(core) < 3 or len(letters) < 2 or upper in _NOT_SYMBOLS or _LABEL_WORD_RE.match(upper):
            continue
        if token.startswith("#") or letters.isupper():
            return _SymbolHit(upper, token, False, match.start(), match.end())
    return None


# --------------------------------------------------------------------------- labels

_B = r"(?<![a-z])"  # no letter before (digits are fine: "2650sl 2640")
_E = r"(?![a-z])"  # no letter after
_AT = r"(?:\s*(?:@|at(?![a-z])))?"  # "SL @ 2640" / "TP1 at 2660" stay one label
_TP_INDEX = r"(?:\s*(?:#|no\.?\s*|[-_]\s*)?(?P<index>[1-9])(?!\d)(?![.,]\d)(?!\s*(?:pips?|points?|pts?|p\b|%)))?"

_LABEL_RE = re.compile(
    "|".join(
        (
            rf"(?P<order>{_B}(?:buy|sell)(?:ing)?\s*[-_]?\s*(?:limit|stop){_E})",
            rf"(?P<sl>(?:{_B}(?:stop[\s_-]*loss|stoploss|s\s*/\s*l|s\.\s?l\.?|sl){_E}|🛑|⛔){_AT})",
            rf"(?P<tp>(?:{_B}(?:take[\s_-]*profit|takeprofit|t\s*/\s*p|t\.\s?p\.?|tp|targets?|tgt){_E}|🎯){_TP_INDEX}{_AT})",
            rf"(?P<entry>{_B}(?:entry(?:[\s_-]*(?:zone|price|point|area|level))?|enter|zone|area|between|price"
            rf"|@|at(?=\s*(?:[:=-]\s*)?\d)){_E}{_AT})",
            rf"(?P<dir>{_B}(?:buy|sell)(?:ing)?{_E}|{_B}(?:long|short|bullish|bearish){_E})",
            rf"(?P<market>{_B}(?:now|market|instant|immediately|cmp){_E})",
            rf"(?P<limit>{_B}limit{_E})",
            rf"(?P<stop>{_B}stop{_E}{_AT})",
            rf"(?P<ignore>{_B}(?:r\s*[:/]\s*r|rr|risk(?:\s*(?:reward|ratio))?|lots?(?:\s*size)?|size|leverage"
            rf"|time|date|tf|timeframe|spread){_E})",
        )
    )
)
_LABEL_KINDS = ("order", "sl", "tp", "entry", "dir", "market", "limit", "stop", "ignore")
_STRUCTURE_KINDS = frozenset({"order", "sl", "tp", "dir"})
_ENTRY_KINDS = ("order", "limit", "dir", "market")
_DIRECTION_TIERS = (
    {"buy": "long", "sell": "short"},
    {"long": "long", "short": "short"},
    {"bullish": "long", "bearish": "short"},
)
_LONG_EMOJI = ("🟢", "📈")
_SHORT_EMOJI = ("🔴", "📉")


# --------------------------------------------------------------------------- numbers

_MASK_RE = re.compile(
    r"(?<![\d.,])\d{1,2}[./-]\d{1,2}[./-]\d{2,4}(?!\d)"  # dates: 20/10/2026
    r"|(?<![\d.,])\d{1,2}(?:\.\d+)?\s?:\s?\d{1,2}(?:\.\d+)?(?!\d)"  # times 14:30 and ratios 1:3
)
_RUN_RE = re.compile(r"(?<![a-z0-9_#.,])\d(?:[\d.,]*\d)?")
_UNIT_RE = re.compile(r"\s*(?:pips?|points?|pts?|p)(?![a-z])")


@dataclass
class _Token:
    raw: str
    start: int
    end: int
    pips: bool


@dataclass
class _Reading:
    """One number as written; ``choices[0]`` is the default interpretation."""

    choices: list[tuple[float, ...]]
    pips: bool = False
    chosen: tuple[float, ...] = ()


def _interpret(raw: str) -> list[list[tuple[float, ...]]]:
    """Possible readings of a digit run; several entries when the run is a glued list."""
    seps = re.findall(r"[.,]", raw)
    parts = re.split(r"[.,]", raw)
    if not seps:
        return [[(float(raw),)]]
    if len(seps) == 1:
        head, tail = parts
        if seps[0] == "," and len(head) == len(tail) >= 4:  # "2645,2655" - a list without spaces
            return [[(float(head),)], [(float(tail),)]]
        decimal, thousands = float(f"{head}.{tail}"), float(head + tail)
        if len(tail) == 3 and len(head) <= 3 and head.strip("0"):
            # "18,550" / "1.085": thousands separator or decimal mark?
            return [[(thousands,), (decimal,)] if seps[0] == "," else [(decimal,), (thousands,)]]
        return [[(decimal,)]]
    grouped = all(len(part) == 3 for part in parts[1:-1]) and 1 <= len(parts[0]) <= 3
    if len(set(seps)) == 1 and grouped and len(parts[-1]) == 3:  # 1,234,567 / 1.234.567
        return [[(float("".join(parts)),)]]
    if len(set(seps[:-1])) == 1 and seps[-2] != seps[-1] and grouped:  # 2,650.50 / 2.650,50
        return [[(float("".join(parts[:-1]) + "." + parts[-1]),)]]
    if "," in seps:  # "1.0820,1.0790" / "2645,2655,2670"
        return [reading for piece in raw.split(",") if piece for reading in _interpret(piece)]
    return []


def _tokens(region: str) -> list[_Token]:
    tokens: list[_Token] = []
    for match in _RUN_RE.finditer(region):
        rest = region[match.end() :]
        unit = _UNIT_RE.match(rest)
        if unit:
            tokens.append(_Token(match.group(0), match.start(), match.end() + unit.end(), True))
        elif not (rest[:1].isascii() and rest[:1].isalpha()) and not rest.lstrip().startswith("%"):
            tokens.append(_Token(match.group(0), match.start(), match.end(), False))
    return tokens


def _space_grouped(region: str, a: _Token, b: _Token) -> bool:
    """``67 250``: a 1-3 digit integer followed, after one space, by exactly three digits."""
    return (
        region[a.end : b.start] == " "
        and not a.pips
        and re.fullmatch(r"\d{1,3}", a.raw) is not None
        and re.fullmatch(r"\d{3}(?:[.,]\d+)?", b.raw) is not None
    )


def _readings(region: str) -> list[_Reading]:
    """Numbers of a region; space-grouped pairs (``67 250``) become ambiguous readings."""
    region = _MASK_RE.sub(lambda m: " " * len(m.group(0)), region)
    tokens = _tokens(region)
    out: list[_Reading] = []
    i = 0
    while i < len(tokens):
        token = tokens[i]
        nxt = tokens[i + 1] if i + 1 < len(tokens) else None
        after = tokens[i + 2] if i + 2 < len(tokens) else None
        # Only pairs: "100 200 300" is a list, not a number.
        if nxt and _space_grouped(region, token, nxt) and not (after and _space_grouped(region, nxt, after)):
            first, tail = float(token.raw), float(nxt.raw.replace(",", "."))
            out.append(_Reading([(first * 1000 + tail,), (first, tail)], pips=nxt.pips))
            i += 2
            continue
        out += [_Reading(choices, pips=token.pips) for choices in _interpret(token.raw)]
        i += 1
    # Absurd digit runs ("9999...9") overflow to inf; they are noise, never prices.
    for reading in out:
        reading.choices = [c for c in reading.choices if all(math.isfinite(v) for v in c)]
    return [reading for reading in out if reading.choices]


def _settle(readings: list[_Reading], low_priced: bool, high_priced: bool = False) -> None:
    """Pick an interpretation for every reading, using the unambiguous prices as the scale."""
    refs = [v for r in readings if len(r.choices) == 1 and not r.pips for v in r.choices[0] if v > 0]
    scale = statistics.median(refs) if refs else None

    def distance(choice: tuple[float, ...]) -> float:
        return sum(abs(math.log(v / scale)) if v > 0 else 50.0 for v in choice) / len(choice)

    for reading in readings:
        if len(reading.choices) == 1 or reading.pips:
            reading.chosen = reading.choices[0]
        elif scale:
            reading.chosen = min(reading.choices, key=distance)
        elif low_priced:
            reading.chosen = next((c for c in reading.choices if all(v < 1000 for v in c)), reading.choices[0])
        elif high_priced:
            reading.chosen = next((c for c in reading.choices if all(v >= 1000 for v in c)), reading.choices[0])
        else:
            reading.chosen = reading.choices[0]


# --------------------------------------------------------------------------- regions


@dataclass
class _Region:
    kind: str  # prefix | entry | order | limit | dir | market | sl | stop | tp | ignore
    label: str
    start: int
    index: int | None = None
    readings: list[_Reading] = field(default_factory=list)

    @property
    def prices(self) -> list[float]:
        return [v for r in self.readings if not r.pips for v in r.chosen]

    @property
    def pips(self) -> list[float]:
        return [v for r in self.readings if r.pips for v in r.chosen]


def _label_kind(match: re.Match) -> str:
    return next(name for name in _LABEL_KINDS if match.group(name) is not None)


_FREE_LINE_RE = re.compile(r"\n[ \t]*[a-z]")  # a new line that starts with a word


def _regions(work: str) -> list[_Region]:
    labels = list(_LABEL_RE.finditer(work))
    regions = [_Region("prefix", "", 0, readings=_readings(work[: labels[0].start() if labels else len(work)]))]
    for i, match in enumerate(labels):
        end = labels[i + 1].start() if i + 1 < len(labels) else len(work)
        kind = _label_kind(match)
        if kind in ("sl", "tp"):
            # "TP 2660\nVIP signal 125": a stop or target ends at a new line of free text.
            free = _FREE_LINE_RE.search(work, match.end(), end)
            end = free.start() if free else end
        index = int(match.group("index")) if kind == "tp" and match.group("index") else None
        readings = [] if kind == "ignore" else _readings(work[match.end() : end])
        regions.append(_Region(kind, match.group(0).strip(), match.start(), index, readings))
    return regions


def _direction(regions: list[_Region], text: str, warnings: list[str]) -> str | None:
    for tier in _DIRECTION_TIERS:
        found = []
        for region in regions:
            if region.kind in ("dir", "order"):
                word = re.match(r"[a-z]+", region.label)
                stem = word.group(0).removesuffix("ing") if word else ""
                if stem in tier:
                    found.append((tier[stem], stem))
        if found:
            if len({direction for direction, _ in found}) > 1:
                warnings.append(f"Both buy and sell words found - using '{found[0][1].upper()}' (first mention).")
            return found[0][0]
    longs = sum(text.count(e) for e in _LONG_EMOJI)
    shorts = sum(text.count(e) for e in _SHORT_EMOJI)
    if longs != shorts:
        return "long" if longs > shorts else "short"
    return None


def _order_type(regions: list[_Region]) -> str:
    for region in regions:
        if region.kind == "order":
            return "limit" if "limit" in region.label else "stop"
        if region.kind == "limit":
            return "limit"
    return "market"


# --------------------------------------------------------------------------- assembly


def parse_signal(text: str, symbols: Iterable[str] = ()) -> ParsedSignal:
    """Extract symbol, direction, entry (or zone), stop and targets from a signal message.

    ``symbols`` are the configured market symbols: a ticker that matches one of
    them (directly or through an alias such as GOLD -> XAUUSD) is preferred and
    returned in its configured spelling; anything else gets a warning.
    """
    result = ParsedSignal()
    warnings = result.warnings
    text = _normalise(text or "")
    if not text.strip():
        warnings.append("Nothing to parse - paste a signal message.")
        return result

    book = _SymbolBook(list(symbols))
    hits = _find_symbols(text, book)
    hidden = [(hit.start, hit.end) for hit in hits]
    lower = text.translate(_ASCII_LOWER)
    chosen = next((hit for hit in hits if hit.configured), hits[0] if hits else None)
    if chosen is None and any(_label_kind(m) in _STRUCTURE_KINDS for m in _LABEL_RE.finditer(lower)):
        # Only look for an unknown ticker when the text reads like a signal at all.
        chosen = _unknown_symbol(text, hidden)
        if chosen:
            hidden.append((chosen.start, chosen.end))
            warnings.append(f"'{chosen.raw}' is not a known instrument - check the symbol.")
    elif chosen is not None and not chosen.configured and book.configured:
        warnings.append(f"{chosen.symbol} is not one of your configured markets.")
    if chosen:
        result.symbol, result.symbol_raw = chosen.symbol, chosen.raw
        others = sorted({hit.symbol for hit in hits} - {chosen.symbol})
        if others:
            warnings.append(f"Several instruments mentioned ({', '.join(others)}) - using {chosen.symbol}.")

    work = lower
    for start, end in hidden:
        work = work[:start] + " " * (end - start) + work[end:]
    regions = _regions(work)
    code = (result.symbol or "").upper()
    low_priced = _is_fx_pair(code) and code[:3] in _LOW_PRICED and code[3:] in _LOW_PRICED
    _settle([r for region in regions for r in region.readings], low_priced, code in _HIGH_PRICED)

    result.direction = _direction(regions, text, warnings)
    result.order_type = _order_type(regions)
    _assemble_entry(result, regions)
    _assemble_stop(result, regions)
    open_targets = _assemble_targets(result, regions)
    _relative_by_scale(result)
    _resolve_zone(result)
    _check_geometry(result)

    if not (result.symbol or result.direction or result.stop_loss or result.stop_pips or result.take_profits):
        # A stray number or label is not a signal: report nothing but the reason.
        return ParsedSignal(warnings=["No trade signal recognised - expected a symbol, BUY/SELL, entry, SL and TP."])
    if open_targets:
        one = len(open_targets) == 1
        warnings.append(
            f"{_fmt_list(open_targets)} {'has' if one else 'have'} no price (open / runner) - skipped; "
            f"manage {'it' if one else 'them'} manually."
        )
    return result


def _assemble_entry(result: ParsedSignal, regions: list[_Region]) -> None:
    """Entry (or zone), most specific first: the price after BUY/SELL, an entry label before the stop and
    targets, an entry-like phrase after them ("add more at 2645", "move SL to BE at 2655" are management
    notes, used only when nothing else gives an entry), then a bare leading number."""
    structure = next((r.start for r in regions if r.kind in ("sl", "stop", "tp")), None)
    late = [r for r in regions if r.kind == "entry" and r.prices and structure is not None and r.start > structure]
    candidates = (
        [r for r in regions if r.kind in _ENTRY_KINDS and r.prices]
        or [r for r in regions if r.kind == "entry" and r.prices and r not in late]
        or late
        or [r for r in regions if r.kind == "prefix" and r.prices]
    )
    if not candidates:
        return
    prices = candidates[0].prices
    others = [
        r for r in regions if (r.kind == "entry" or r.kind in _ENTRY_KINDS) and r.prices and set(r.prices) - set(prices)
    ]
    if others:
        result.warnings.append(
            f"Entry {_fmt_list(prices)} used; '{others[0].label} {_fmt_list(others[0].prices)}' also looks like a "
            "price to enter at - check the entry."
        )
    if len(prices) == 1:
        result.entry = prices[0]
        return
    result.entry_low, result.entry_high = min(prices), max(prices)
    if len(prices) > 2:
        result.warnings.append(
            f"{len(prices)} entry prices found - treated as the zone {_fmt(result.entry_low)}-{_fmt(result.entry_high)}."
        )


def _assemble_stop(result: ParsedSignal, regions: list[_Region]) -> None:
    stops = [r for r in regions if r.kind in ("sl", "stop") and (r.prices or r.pips)]
    if not stops:
        return
    first = stops[0]
    if first.prices:
        result.stop_loss = first.prices[0]
    else:
        result.stop_pips = first.pips[0]
    values = {v for region in stops for v in (region.prices or region.pips)}
    if len(values) > 1:
        used = _fmt(result.stop_loss) if result.stop_loss is not None else f"{_fmt(result.stop_pips)} pips"
        result.warnings.append(f"Several stop-loss values found - using the first ({used}).")


def _assemble_targets(result: ParsedSignal, regions: list[_Region]) -> list[str]:
    """Collect TP prices / pips in message order; returns the labels of open-ended targets."""
    open_targets: list[str] = []
    position = 0
    for region in regions:
        if region.kind != "tp" or (region.label == "🎯" and not region.readings):
            continue  # "🎯 TP1: 2660" - the emoji is decoration in front of the real label
        position += 1
        if region.prices:
            result.take_profits += region.prices
        elif region.pips:
            result.target_pips += region.pips
        else:
            open_targets.append(f"TP{region.index or position}")
    return open_targets


def _relative_by_scale(result: ParsedSignal) -> None:
    """Read "SL 30" / "TP 50" next to a 2650 entry as distances in pips."""
    ref = result.entry if result.entry is not None else result.entry_high
    if ref is None:
        return
    limit = ref * 0.25
    if result.stop_loss is not None and result.stop_loss < limit:
        result.warnings.append(
            f"SL {_fmt(result.stop_loss)} looks like a distance - read as {_fmt(result.stop_loss)} pips."
        )
        result.stop_pips, result.stop_loss = result.stop_loss, None
    small = [tp for tp in result.take_profits if tp < limit]
    if small:
        result.warnings.append(f"TP {_fmt_list(small)} looks like a distance - read as pips.")
        result.take_profits = [tp for tp in result.take_profits if tp >= limit]
        result.target_pips += small


def _resolve_zone(result: ParsedSignal) -> None:
    """Entry of a zone: the edge farthest from the stop (worst fill), else the midpoint."""
    low, high = result.entry_low, result.entry_high
    if low is None or high is None:
        return
    if result.direction is None and result.stop_loss is not None and not low <= result.stop_loss <= high:
        result.direction = "long" if result.stop_loss < low else "short"
    if result.stop_loss is not None:
        if low < result.stop_loss < high:
            result.warnings.append(
                f"Stop loss {_fmt(result.stop_loss)} lies inside the entry zone {_fmt(low)}-{_fmt(high)}."
            )
        result.entry = max((low, high), key=lambda edge: abs(edge - result.stop_loss))
    elif result.stop_pips is not None and result.direction:
        result.entry = high if result.direction == "long" else low
    else:
        result.entry = round((low + high) / 2, 10)
    if high > 0 and (high - low) / high > 0.05:
        result.warnings.append(f"Entry zone {_fmt(low)}-{_fmt(high)} is unusually wide - check the prices.")


def _check_geometry(result: ParsedSignal) -> None:
    warnings = result.warnings
    entry, stop = result.entry, result.stop_loss
    word = {"long": "BUY", "short": "SELL"}

    if result.direction is None:
        if entry is not None and stop is not None and stop != entry:
            result.direction = "long" if stop < entry else "short"
        elif entry is not None and result.take_profits:
            result.direction = "long" if result.take_profits[0] > entry else "short"
        elif stop is not None and result.take_profits:
            result.direction = "long" if stop < result.take_profits[0] else "short"
    elif entry is not None and stop is not None:
        if (result.direction == "long" and stop >= entry) or (result.direction == "short" and stop <= entry):
            side = "above" if stop > entry else "below" if stop < entry else "at"
            warnings.append(
                f"{word[result.direction]} signal but the stop loss ({_fmt(stop)}) is {side} the entry "
                f"({_fmt(entry)}) - check the direction."
            )

    _order_targets(result)
    if result.direction and result.take_profits:
        sign = 1 if result.direction == "long" else -1
        if entry is not None:
            wrong = [tp for tp in result.take_profits if (tp - entry) * sign <= 0]
            if wrong:
                side = "below" if sign > 0 else "above"
                warnings.append(
                    f"Take profit {_fmt_list(wrong)} is {side} the entry for a "
                    f"{word[result.direction]} - check the signal."
                )
        elif stop is not None and (result.take_profits[0] - stop) * sign <= 0:
            warnings.append(f"Stop loss {_fmt(stop)} is beyond the take profit - check the signal.")


def _order_targets(result: ParsedSignal) -> None:
    targets = list(dict.fromkeys(result.take_profits))
    if len(targets) < len(result.take_profits):
        result.warnings.append("Duplicate take-profit prices removed.")
    if result.direction:
        ordered = sorted(targets, reverse=result.direction == "short")
        if ordered != targets:
            result.warnings.append("Take profits re-ordered nearest to farthest.")
        targets = ordered
    if len(targets) > MAX_TARGETS:
        dropped = targets[MAX_TARGETS:]
        result.warnings.append(
            f"{len(targets)} take profits found - kept TP1-TP{MAX_TARGETS}, dropped {_fmt_list(dropped)}."
        )
        targets = targets[:MAX_TARGETS]
    result.take_profits = targets
    room = MAX_TARGETS - len(targets)
    pips = sorted(dict.fromkeys(result.target_pips))
    if len(pips) > room:
        result.warnings.append(f"Relative targets beyond TP{MAX_TARGETS} dropped ({_fmt_list(pips[room:])} pips).")
    result.target_pips = pips[:room]


# --------------------------------------------------------------------------- ticket helpers


def resolve_close_pcts(given: list[float | None]) -> list[float]:
    """Close % per target: the default split when none is given, else the rest shared by the blanks.

    ``[None, None, None]`` -> ``[50, 25, 25]``; ``[60, None, None]`` -> ``[60, 20, 20]``.
    Raises ``ValueError`` when the given shares leave nothing for the blank targets.
    """
    if not given:
        return []
    if all(pct is None for pct in given):
        return [float(pct) for pct in DEFAULT_TP_SPLITS[len(given)]]
    blanks = [i for i, pct in enumerate(given) if pct is None]
    if not blanks:
        return [float(pct) for pct in given]
    remainder = 100.0 - sum(pct for pct in given if pct is not None)
    if remainder <= 0.005:
        raise ValueError(
            f"The close % already set add up to {100 - remainder:g}% - nothing is left for "
            + ", ".join(f"TP{i + 1}" for i in blanks)
            + "."
        )
    share = round(remainder / len(blanks), 2)
    out = [float(pct) if pct is not None else share for pct in given]
    out[blanks[-1]] = round(remainder - share * (len(blanks) - 1), 2)  # absorb the rounding
    return out


def split_lots(total: float, close_pcts: list[float], lot_step: float) -> list[float]:
    """Lots closed at each target: shares floored to the lot step, the last target takes the rest."""
    if not close_pcts:
        return []
    step = Decimal(str(lot_step))
    remaining = Decimal(str(total))
    out: list[Decimal] = []
    for pct in close_pcts[:-1]:
        share = (Decimal(str(total)) * Decimal(str(pct)) / 100 / step).to_integral_value(rounding=ROUND_DOWN) * step
        share = min(share, remaining)
        out.append(share)
        remaining -= share
    out.append(remaining)
    return [float(lots) for lots in out]
