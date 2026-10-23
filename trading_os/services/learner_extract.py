"""AI Strategy Learner, the pure part: transcript cleaning, the strategy draft and the offline rule extractor.

``StrategyDraft`` is the one shape every engine produces and the trader edits: markets, timeframes, sessions,
direction, confluences, entry / stop / exit rules, risk parameters, filters, what is missing and warnings.
``DRAFT_SCHEMA`` is the same shape as a structured-output JSON schema for Claude.

``basic_extract`` reads English or Italian text without AI: it finds rule sentences by keyword (or, in captions
without punctuation, a window of words around the keyword), timeframes, sessions, markets, confluences and the
numbers a text states (risk %, reward:risk, trades a day, daily loss). It never invents a value: what it
cannot find is listed as missing. Claude reads the text far better; this keeps the Learner useful offline.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Annotated, Any, Literal

from pydantic import BeforeValidator, Field, field_validator

from ..constants import TIMEFRAMES
from ..schemas import FiniteModel
from .i18n import N_, tr

# ------------------------------------------------------------------------------ limits

NAME_MAX, SUMMARY_MAX, RULE_MAX, DETAIL_MAX, MARKET_MAX, SESSION_MAX = 60, 600, 300, 200, 30, 40
MAX_RULES, MAX_CONFLUENCES, MAX_MARKETS, MAX_SESSIONS, MAX_NOTES = 12, 12, 10, 6, 8
CONFLUENCE_NAME_MAX = 80
DIRECTIONS = ("both", "long", "short")


def _text(value: Any, limit: int) -> str:
    if not isinstance(value, str):
        return ""
    return re.sub(r"\s+", " ", value).strip()[:limit].strip()


def _texts(limit: int, count: int):
    """A list of short texts: trimmed, single-spaced, without blanks or repeats, at most ``count``."""

    def clean(value: Any) -> list[str]:
        if not isinstance(value, list):
            return []
        out: list[str] = []
        seen: set[str] = set()
        for item in value:
            text = _text(item, limit)
            if text and text.lower() not in seen:
                seen.add(text.lower())
                out.append(text)
            if len(out) == count:
                break
        return out

    return BeforeValidator(clean)


def normalize_timeframe(value: Any) -> str | None:
    """'M15', 'm15', '15m', '15 min', '15-minute', 'H4', '4h', '4 hour', 'D1', 'daily', 'W1', 'weekly' -> a code."""
    if not isinstance(value, str):
        return None
    text = value.strip().lower().replace("-", " ")
    named = {"daily": "D1", "day": "D1", "1d": "D1", "d": "D1", "weekly": "W1", "week": "W1", "1w": "W1", "w": "W1"}
    named |= {"hourly": "H1", "giornaliero": "D1", "settimanale": "W1", "orario": "H1"}
    if text in named:
        return named[text]
    match = re.fullmatch(r"([mhdw])\s?(\d{1,2})", text) or None
    if match:
        code = f"{match.group(1).upper()}{int(match.group(2))}"
        return code if code in TIMEFRAMES else None
    match = re.fullmatch(r"(\d{1,2})\s?(m|min|mins|minute|minutes|minuto|minuti)", text)
    if match:
        code = f"M{int(match.group(1))}"
        return code if code in TIMEFRAMES else None
    match = re.fullmatch(r"(\d{1,2})\s?(h|hr|hrs|hour|hours|ora|ore)", text)
    if match:
        code = f"H{int(match.group(1))}"
        return code if code in TIMEFRAMES else None
    return None


def _timeframes(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    found = {code for code in (normalize_timeframe(item) for item in value) if code}
    return [code for code in TIMEFRAMES if code in found]  # shortest first, like the rest of the app


def _number(low: float, high: float, integer: bool = False):
    """Out-of-range or unreadable numbers from a model become None (the trader fills them in)."""

    def clean(value: Any) -> float | int | None:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return None
        if value != value or not (low < value <= high if not integer else low <= value <= high):  # NaN / range
            return None
        if integer and not float(value).is_integer():
            return None
        return int(value) if integer else round(float(value), 4)

    return clean


class ConfluenceDraft(FiniteModel):
    name: Annotated[str, BeforeValidator(lambda v: _text(v, CONFLUENCE_NAME_MAX))] = ""
    required: bool = False
    detail: Annotated[str, BeforeValidator(lambda v: _text(v, DETAIL_MAX))] = ""


class RiskDraft(FiniteModel):
    risk_per_trade_pct: float | None = Field(None, gt=0, le=100)
    min_rr: float | None = Field(None, gt=0, le=50)
    max_trades_per_day: int | None = Field(None, ge=1, le=100)
    max_daily_loss_pct: float | None = Field(None, gt=0, le=100)
    notes: Annotated[list[str], _texts(RULE_MAX, MAX_NOTES)] = Field(default_factory=list)


class StrategyDraft(FiniteModel):
    """A strategy as the Learner understood it; every field can be edited before it goes to the incubator."""

    name: Annotated[str, BeforeValidator(lambda v: _text(v, NAME_MAX))] = ""
    summary: Annotated[str, BeforeValidator(lambda v: _text(v, SUMMARY_MAX))] = ""
    markets: Annotated[list[str], _texts(MARKET_MAX, MAX_MARKETS)] = Field(default_factory=list)
    timeframes: Annotated[list[str], BeforeValidator(_timeframes)] = Field(default_factory=list)
    sessions: Annotated[list[str], _texts(SESSION_MAX, MAX_SESSIONS)] = Field(default_factory=list)
    direction: Literal["both", "long", "short"] = "both"
    confluences: list[ConfluenceDraft] = Field(default_factory=list)
    entry_rules: Annotated[list[str], _texts(RULE_MAX, MAX_RULES)] = Field(default_factory=list)
    exit_rules: Annotated[list[str], _texts(RULE_MAX, MAX_RULES)] = Field(default_factory=list)
    stop_loss: Annotated[str, BeforeValidator(lambda v: _text(v, RULE_MAX))] = ""
    risk: RiskDraft = Field(default_factory=RiskDraft)
    filters: Annotated[list[str], _texts(RULE_MAX, MAX_NOTES)] = Field(default_factory=list)
    missing: Annotated[list[str], _texts(RULE_MAX, MAX_NOTES)] = Field(default_factory=list)
    warnings: Annotated[list[str], _texts(RULE_MAX, MAX_NOTES)] = Field(default_factory=list)

    @field_validator("direction", mode="before")
    @classmethod
    def _direction(cls, value: Any) -> str:
        return value if value in DIRECTIONS else "both"

    @field_validator("confluences", mode="before")
    @classmethod
    def _confluences(cls, value: Any) -> list:
        if not isinstance(value, list):
            return []
        out, seen = [], set()
        for item in value:
            if isinstance(item, str):
                item = {"name": item}
            if not isinstance(item, dict):
                continue
            name = _text(item.get("name"), CONFLUENCE_NAME_MAX)
            if name and name.lower() not in seen:
                seen.add(name.lower())
                out.append({**item, "name": name})
        return out[:MAX_CONFLUENCES]

    def is_empty(self) -> bool:
        return not (self.entry_rules or self.exit_rules or self.stop_loss or self.confluences)


def model_draft(raw: Any) -> StrategyDraft:
    """A draft from a model's answer: numbers out of range become None instead of failing the whole answer."""
    raw = dict(raw) if isinstance(raw, dict) else {}
    risk = raw.get("risk") if isinstance(raw.get("risk"), dict) else {}
    raw["risk"] = {
        "risk_per_trade_pct": _number(0, 100)(risk.get("risk_per_trade_pct")),
        "min_rr": _number(0, 50)(risk.get("min_rr")),
        "max_trades_per_day": _number(1, 100, integer=True)(risk.get("max_trades_per_day")),
        "max_daily_loss_pct": _number(0, 100)(risk.get("max_daily_loss_pct")),
        "notes": risk.get("notes"),
    }
    return StrategyDraft.model_validate(raw)


# -------------------------------------------------------------- structured-output schema


def _obj(**properties: Any) -> dict:
    return {"type": "object", "properties": properties, "required": list(properties), "additionalProperties": False}


def _array(items: dict) -> dict:
    return {"type": "array", "items": items}


def _nullable(schema: dict) -> dict:
    return {"anyOf": [schema, {"type": "null"}]}


_STR = {"type": "string"}
DRAFT_SCHEMA: dict = _obj(
    name=_STR,
    summary=_STR,
    markets=_array(_STR),
    timeframes=_array({"type": "string", "enum": list(TIMEFRAMES)}),
    sessions=_array(_STR),
    direction={"type": "string", "enum": list(DIRECTIONS)},
    confluences=_array(_obj(name=_STR, required={"type": "boolean"}, detail=_STR)),
    entry_rules=_array(_STR),
    exit_rules=_array(_STR),
    stop_loss=_STR,
    risk=_obj(
        risk_per_trade_pct=_nullable({"type": "number"}),
        min_rr=_nullable({"type": "number"}),
        max_trades_per_day=_nullable({"type": "integer"}),
        max_daily_loss_pct=_nullable({"type": "number"}),
        notes=_array(_STR),
    ),
    filters=_array(_STR),
    missing=_array(_STR),
    warnings=_array(_STR),
)
ANSWER_SCHEMA: dict = _obj(reply=_STR, draft=DRAFT_SCHEMA)


# ------------------------------------------------------------------------- transcripts

_TIMESTAMP_LINE = re.compile(r"^\s*[\[(]?(?:\d{1,2}:)?\d{1,2}:\d{2}[\])]?(?:\s+|$)")
_TIMESTAMP_BRACKETED = re.compile(r"[\[(](?:\d{1,2}:)?\d{1,2}:\d{2}[\])]")
_CAPTION_TAG = re.compile(
    r"\[(?:music|applause|laughter|silence|inaudible|musica|applausi|risate|silenzio|incomprensibile)\]|"
    r"\((?:music|applause|laughter|musica|applausi|risate)\)|[♪♫]+|^\s*>>\s*",
    re.IGNORECASE | re.MULTILINE,
)


@dataclass
class CleanResult:
    text: str
    timestamps: int = 0
    tags: int = 0
    words: int = 0
    chars_before: int = 0


def clean_transcript(raw: str) -> CleanResult:
    """A pasted transcript as plain text: timestamps (when most lines start with one), caption tags such as
    [Music] and speaker arrows removed, broken caption lines joined, whitespace collapsed."""
    raw = (raw or "").replace("\r\n", "\n").replace("\r", "\n")
    lines = [line for line in raw.split("\n") if line.strip()]
    stamped = sum(1 for line in lines if _TIMESTAMP_LINE.match(line))
    timestamps = 0
    if stamped >= 3 and stamped >= len(lines) * 0.3:  # a transcript with times, not prose with an "8:30" in it
        kept = []
        for line in lines:
            line, count = _TIMESTAMP_LINE.subn("", line, count=1)
            timestamps += count
            if line.strip():
                kept.append(line)
        lines = kept
    text = "\n".join(lines)
    text, bracketed = _TIMESTAMP_BRACKETED.subn(" ", text)
    text, tags = _CAPTION_TAG.subn(" ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return CleanResult(text, timestamps + bracketed, tags, len(text.split()), len(raw))


# --------------------------------------------------------------------------- language

_IT_WORDS = frozenset(
    "il lo la gli le un una che di del della per non è sono quando questo questa come anche ma se poi prezzo "
    "candela candele entrata entro entrare uscita sotto sopra perché quindi allora ancora sempre solo".split()
)
_EN_WORDS = frozenset(
    "the a an and to of is are when this that how also but if then price candle candles entry enter exit below "
    "above because so you we it on with only always".split()
)


def detect_language(text: str) -> str:
    """'it' or 'en' from the share of common words (English when unsure)."""
    words = re.findall(r"[a-zàèéìòù]+", (text or "").lower())
    italian = sum(1 for w in words if w in _IT_WORDS)
    english = sum(1 for w in words if w in _EN_WORDS)
    return "it" if italian > english * 1.2 and italian >= 3 else "en"


# ---------------------------------------------------------------------- basic extractor

_FLAGS = re.IGNORECASE
ENTRY_RE = re.compile(
    r"\b(?:enter|entry|entries|get in|go long|go short|buy (?:when|if|at|on|the)|sell (?:when|if|at|on|the)|"
    r"long (?:when|if|on|at)|short (?:when|if|on|at)|trigger\w*|place (?:a|the|my) (?:buy|sell|order|limit)|"
    r"buy limit|sell limit|buy stop|sell stop|open (?:a|the) (?:trade|position)|wait for|confirmation|"
    r"entra\w*|ingress\w*|compr[ao]\w*|vend[oi]\b|vendere|aspett\w*|attend\w*|conferma\w*)",
    _FLAGS,
)
STOP_RE = re.compile(r"\b(?:stop[- ]?loss\w*|stop(?![- ]?hunt)\b|sl\b|invalidat\w*|invalida\w*)", _FLAGS)
EXIT_RE = re.compile(
    r"\b(?:take[- ]?profit\w*|tp\d?\b|targets?\b|exit\w*|close (?:the|half|part|my|it|a)|partials?\b|trail\w*|"
    r"break[- ]?even|scale out|chiud\w*|uscit\w*|obiettiv\w*|parzial\w*|pareggio|prend\w* profitt\w*)",
    _FLAGS,
)
FILTER_RE = re.compile(
    r"(?:\bavoid\w*|\bdon'?t trade|\bdo not trade|\bnever trade|\bno trades?\b|\bstay out|\bnews\b|\bnfp\b|\bfomc\b|"
    r"\bcpi\b|\bfridays?\b|\bevita\w*|\bnon (?:operare|tradare|entrare|fare trading|faccio trading)|\bnotizi\w*|"
    r"\bvenerd[iì]\b)",
    _FLAGS,
)
MUST_RE = re.compile(
    r"\b(?:must|always|only (?:if|when)|need\w*|required|has to|have to|devi|deve|sempre|solo (?:se|quando)|"
    r"necessari\w*|obbligatori\w*|serve|servono)\b",
    _FLAGS,
)
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?;])\s+|\n+")
_LONG_SENTENCE = 260
_WINDOW_BEFORE, _WINDOW_AFTER = 8, 24


def _sentences(text: str) -> list[str]:
    return [s.strip() for s in _SENTENCE_SPLIT.split(text) if s.strip()]


def _snippets(text: str, pattern: re.Pattern, limit: int) -> list[str]:
    """Sentences that match; in long unpunctuated captions, a window of words around each match."""
    out: list[str] = []
    for sentence in _sentences(text):
        if not pattern.search(sentence):
            continue
        if len(sentence) <= _LONG_SENTENCE:
            out.append(sentence)
        else:
            words = sentence.split()
            last_end = -1
            for match in pattern.finditer(sentence):
                at = len(sentence[: match.start()].split())
                if at <= last_end:
                    continue
                start, end = max(0, at - _WINDOW_BEFORE), min(len(words), at + _WINDOW_AFTER)
                snippet = " ".join(words[start:end])
                out.append(("… " if start else "") + snippet + (" …" if end < len(words) else ""))
                last_end = end
        if len(out) >= limit * 2:
            break
    cleaned = StrategyDraft.model_validate({"entry_rules": out}).entry_rules  # trim, dedupe, cap
    return cleaned[:limit]


# Confluences the extractor knows: (name in the default library, pattern). The trader's own library names are
# matched too (literally), and a library name that equals one of these is used as written there.
CONFLUENCE_PATTERNS: tuple[tuple[str, str], ...] = (
    ("Liquidity sweep", r"liquidity (?:sweep|grab|raid)|sweep\w*|stop hunt\w*|presa di liquidit\w*|caccia agli stop"),
    (
        "Break of structure",
        r"break of structure|\bbos\b|\bchoch\b|change of character|market structure shift|\bmss\b|rottura (?:di|della) struttura",
    ),
    ("Order block / FVG", r"order ?blocks?|\bob\b|fair value gaps?|\bfvg\b|imbalance|squilibri\w*"),
    ("Key level (S/R)", r"key levels?|support\b|resistance|\bs/r\b|supporto|supporti|resistenz\w*|livell\w* chiave"),
    (
        "HTF trend alignment",
        r"higher time ?frame|\bhtf\b|with the trend|trend direction|\b(?:h1|h4|d1|w1|1h|4h|daily|weekly) trend|"
        r"direzione del trend|a favore (?:di|del) trend|trend principale|trend (?:in|su) (?:h4|h1|d1|daily)",
    ),
    ("Session open / killzone", r"kill ?zones?|session open|opening bell|apertura (?:di|della) sessione"),
    ("Fibonacci OTE", r"fibonacci|\bfib\b|\bote\b|61[.,]8|78[.,]6|golden pocket"),
    ("Momentum / volume", r"momentum|displacement|\bvolumes?\b|volumi"),
    ("Moving average", r"\b(?:ema|sma|moving averages?|medi[ae] mobil[ei])\b"),
    ("RSI", r"\brsi\b|relative strength|overbought|oversold|ipercomprat\w*|ipervendut\w*"),
    ("MACD", r"\bmacd\b"),
    ("VWAP", r"\bvwap\b"),
    ("Bollinger Bands", r"bollinger"),
    ("Trendline", r"trend ?lines?|linea di tendenza|trendline"),
    ("Candlestick pattern", r"engulfing|pin ?bars?|hammer|doji|inside bars?|martello|candela di inversione"),
    ("Divergence", r"divergen\w*"),
    (
        "Supply / demand zone",
        r"supply zones?|demand zones?|supply and demand|zona di (?:domanda|offerta)|domanda e offerta",
    ),
    (
        "Previous day high/low",
        r"\bpdh\b|\bpdl\b|previous day(?:'s)? (?:high|low)|massimo (?:del giorno )?precedente|minimo (?:del giorno )?precedente",
    ),
    ("Opening range", r"opening range|\borb\b|range di apertura"),
)
_COMPILED_CONFLUENCES = tuple((name, re.compile(pattern, _FLAGS)) for name, pattern in CONFLUENCE_PATTERNS)

MARKET_ALIASES: dict[str, str] = {
    "gold": "XAUUSD", "xau": "XAUUSD", "oro": "XAUUSD", "silver": "XAGUSD", "argento": "XAGUSD",
    "nasdaq": "NAS100", "nas100": "NAS100", "us100": "NAS100", "nq": "NAS100", "dow jones": "US30", "dow": "US30",
    "us30": "US30", "s&p 500": "SPX500", "s&p500": "SPX500", "s&p": "SPX500", "sp500": "SPX500", "spx": "SPX500",
    "us500": "SPX500", "dax": "GER40", "ger40": "GER40", "ger30": "GER40", "bitcoin": "BTCUSD", "btc": "BTCUSD",
    "ethereum": "ETHUSD", "eth": "ETHUSD", "crude oil": "USOIL", "oil": "USOIL", "wti": "USOIL", "petrolio": "USOIL",
}  # fmt: skip
_CURRENCIES = "EUR|USD|GBP|JPY|CHF|AUD|CAD|NZD"
_FX_PAIR = re.compile(rf"\b({_CURRENCIES})\s?/?\s?({_CURRENCIES})\b", _FLAGS)

# "the Asian low" or "the London high" is a price level, not a session to trade in.
_LEVEL_AFTER = r"(?!\s+(?:session\s+)?(?:low|high|lows|highs|range|liquidity|open price|close price)\b)"
SESSION_PATTERNS: tuple[tuple[str, re.Pattern], ...] = (
    ("London", re.compile(rf"\b(?:london|londra|ldn)\b{_LEVEL_AFTER}", _FLAGS)),
    (
        "New York",
        re.compile(
            rf"\b(?:new york|ny (?:session|open|am|pm)|us session|sessione americana|apertura americana)\b{_LEVEL_AFTER}",
            _FLAGS,
        ),
    ),
    ("Asia", re.compile(rf"\b(?:asia|asian|tokyo|sessione asiatica|asiatica)\b{_LEVEL_AFTER}", _FLAGS)),
)

_TIMEFRAME_PATTERNS = (
    re.compile(r"\b([mhdw])(\d{1,2})\b", _FLAGS),  # M15, H4, D1, W1
    re.compile(r"\b(\d{1,2})(m|h)\b", _FLAGS),  # 15m, 4h
    re.compile(
        r"\b(\d{1,2})[- ]?(min(?:ute)?s?|minut[oi]|hours?|hr|or[ae])\s+(?:chart|time ?frame|candles?|tf)\b", _FLAGS
    ),
    re.compile(
        r"\b(?:chart|time ?frame|grafico|candel[ae]|tf)\s+(?:a\s+|da\s+|di\s+)?(\d{1,2})\s?(min(?:ute)?s?|minut[oi]|m|"
        r"or[ae]|hours?|h)\b",
        _FLAGS,
    ),
)
_DAILY = re.compile(
    r"\b(?:daily (?:chart|time ?frame|candles?|close|bias|structure)|on the daily|the daily\b|grafico giornaliero|"
    r"time ?frame giornaliero|candela giornaliera|\bd1\b)",
    _FLAGS,
)
_WEEKLY = re.compile(
    r"\b(?:weekly (?:chart|time ?frame|candles?|close)|on the weekly|grafico settimanale|\bw1\b)", _FLAGS
)
_HOURLY = re.compile(r"\b(?:hourly chart|grafico orario|one hour chart|1 hour chart)\b", _FLAGS)

_NUM = r"(\d+(?:[.,]\d+)?)(?!\d|[.,]\d)"  # a whole number, never the first digits of a longer one
RR_EXTRACT_MAX = 20  # a "1:30" in a text is more likely a time than a reward:risk
_PCT = r"\s?(?:%|percent\b|per ?cent\b|per cento\b|percento\b)"
RISK_PCT_RE = (
    re.compile(rf"\b(?:risk(?:ing)?|rischi\w*)\b[^.%\d]{{0,25}}?{_NUM}{_PCT}", _FLAGS),
    re.compile(
        rf"{_NUM}{_PCT}\s*(?:risk\b|of (?:my|the|your) (?:account|balance)|per trade|a trade|di rischio|del conto|"
        r"per operazione|a operazione)",
        _FLAGS,
    ),
)
RR_RE = (
    re.compile(
        rf"\b(?:risk[- ]?(?:to[- ])?reward|reward[- ]?(?:to[- ])?risk|r\s?[:/]\s?r|\brr\b|rapporto rischio[- /]?"
        rf"(?:rendimento|beneficio)|rapporto)\b[^.\d]{{0,20}}?(?:1\s?[:/]\s?)?{_NUM}",
        _FLAGS,
    ),
    re.compile(rf"\b1\s?:\s?{_NUM}(?!\s?(?:am|pm)\b)(?!:\d)", _FLAGS),  # "1:2" (never a clock time)
    re.compile(rf"\b(?:at least|minimum|min|target|almeno|minimo|obiettivo)\s+(?:of\s+|di\s+)?{_NUM}\s?r\b", _FLAGS),
    re.compile(rf"\b(?:take[- ]?profit|tp|targets?|profitt\w*|obiettiv\w*)\b[^.\d]{{0,15}}?{_NUM}\s?r\b", _FLAGS),
    re.compile(rf"\b1\s+(?:to|a)\s+{_NUM}\s+(?:risk[- ]?(?:to[- ])?reward|rr|r:r|rapporto)", _FLAGS),
)
_COUNT_WORDS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "uno": 1, "una": 1, "un": 1, "due": 2}
_COUNT_WORDS |= {"tre": 3, "quattro": 4, "cinque": 5}
_COUNT = r"(\d{1,2}|one|two|three|four|five|uno|una|un|due|tre|quattro|cinque)"
TRADES_RE = re.compile(
    rf"\b{_COUNT}\s+(?:trades?|entries|positions?|setups?|operazion[ei]|entrate|posizion[ei])\s+"
    r"(?:a|per|al|each|every|ogni)\s+(?:day|giorno|session|sessione)\b",
    _FLAGS,
)
DAILY_LOSS_RE = re.compile(
    rf"\b(?:daily (?:loss|drawdown|stop)|max(?:imum)? loss (?:per|a) day|perdita (?:massima )?giornaliera|"
    rf"stop giornaliero|drawdown giornaliero)\b[^.%\d]{{0,25}}?{_NUM}{_PCT}",
    _FLAGS,
)
LONG_ONLY_RE = re.compile(
    r"\b(?:only (?:longs?|buys?|buying)|longs? only|buys? only|solo (?:long|acquisti|buy|in acquisto)|"
    r"soltanto (?:long|acquisti))\b",
    _FLAGS,
)
SHORT_ONLY_RE = re.compile(
    r"\b(?:only (?:shorts?|sells?|selling)|shorts? only|sells? only|solo (?:short|vendite|sell|in vendita)|"
    r"soltanto (?:short|vendite))\b",
    _FLAGS,
)
NAME_RE = (
    re.compile(r"^\s*(?:strategy|strategia|setup)\s*(?:name|nome)?\s*[:\-–]\s*([^\n.]{3,60})", _FLAGS),
    re.compile(
        r"\b(?:strategy|strategia|setup|system|sistema|method|metodo)\s+(?:is\s+|è\s+)?(?:called|named|chiamat[ao]|"
        r"si chiama)\s+[\"“'«]?([^\"”'».\n,]{3,50})",
        _FLAGS,
    ),
)
NO_STOP_RE = re.compile(
    r"\b(?:no stop(?:[- ]?loss)?|without (?:a |any )?stop|don'?t use (?:a |any )?stop|senza stop|"
    r"non (?:uso|metto|usare|mettere) (?:lo |nessuno )?stop)\b",
    _FLAGS,
)
MARTINGALE_RE = re.compile(
    r"\b(?:martingale?|double (?:the|your|my) (?:size|lots?|position)|averag(?:e|ing) down|raddoppi\w*|"
    r"mediare (?:al ribasso|le perdite|in perdita))\b",
    _FLAGS,
)
GUARANTEE_RE = re.compile(
    r"(?:\bguarantee\w*|\b100\s?% (?:win|accura\w*|sicur\w*)|\bnever lose|\bcan'?t lose|\brisk[- ]free|"
    r"\bgarantit\w*|\bnon perdi mai|\bsenza rischi\w*|\bvinci sempre)",
    _FLAGS,
)


@dataclass
class PlanContext:
    """What the Learner knows about the trader's own plan (names to reuse, limits to compare with)."""

    markets: list[str] = field(default_factory=list)  # symbols in the journal
    sessions: list[str] = field(default_factory=list)  # session names
    confluences: list[str] = field(default_factory=list)  # library names
    strategies: list[str] = field(default_factory=list)  # existing strategy names
    max_risk_pct: float | None = None
    min_rr: float | None = None
    max_daily_loss_pct: float | None = None
    max_trades_per_day: int | None = None


def _float(text: str) -> float:
    return float(text.replace(",", "."))


def _first_number(patterns, text: str, low: float, high: float) -> float | None:
    for pattern in patterns:
        for match in pattern.finditer(text):
            try:
                value = _float(match.group(1))
            except ValueError:
                continue
            if low < value <= high:
                return value
    return None


def _markets(text: str, plan: PlanContext) -> list[str]:
    found: list[tuple[int, str]] = []
    known = {symbol.upper(): symbol for symbol in plan.markets}
    for symbol in plan.markets:
        for match in re.finditer(rf"\b{re.escape(symbol)}\b", text, _FLAGS):
            found.append((match.start(), symbol))
    for alias, symbol in MARKET_ALIASES.items():
        for match in re.finditer(rf"(?<![\w&]){re.escape(alias)}(?![\w&])", text, _FLAGS):
            found.append((match.start(), known.get(symbol, symbol)))
    for match in _FX_PAIR.finditer(text):
        base, quote = match.group(1).upper(), match.group(2).upper()
        if base != quote:
            symbol = base + quote
            found.append((match.start(), known.get(symbol, symbol)))
    ordered: list[str] = []
    for _, symbol in sorted(found):
        if symbol not in ordered:
            ordered.append(symbol)
    return ordered[:MAX_MARKETS]


def _timeframes_in(text: str) -> list[str]:
    found: set[str] = set()
    for pattern in _TIMEFRAME_PATTERNS:
        for match in pattern.finditer(text):
            a, b = match.group(1), match.group(2)
            value = f"{a}{b}" if a.isalpha() else f"{a} {b}"
            code = normalize_timeframe(value)
            if code:
                found.add(code)
    if _DAILY.search(text):
        found.add("D1")
    if _WEEKLY.search(text):
        found.add("W1")
    if _HOURLY.search(text):
        found.add("H1")
    return [code for code in TIMEFRAMES if code in found]


def _sessions(text: str, plan: PlanContext) -> list[str]:
    by_lower = {name.lower(): name for name in plan.sessions}
    found: list[str] = []
    for canonical, pattern in SESSION_PATTERNS:
        if pattern.search(text):
            found.append(by_lower.get(canonical.lower(), canonical))
    for name in plan.sessions:
        if name not in found and re.search(rf"\b{re.escape(name)}\b", text, _FLAGS):
            found.append(name)
    return found[:MAX_SESSIONS]


def _detail(name: str, text: str) -> str:
    if name == "Moving average":
        periods = re.findall(r"\b(ema|sma|ma)\s?\(?(\d{1,3})\)?", text, _FLAGS)
        return " / ".join(dict.fromkeys(f"{kind.upper()} {period}" for kind, period in periods))[:DETAIL_MAX]
    if name == "RSI":
        period = re.search(r"\brsi\s?\(?(\d{1,2})\)?", text, _FLAGS)
        levels = re.search(r"\b(70|80)\s?/\s?(30|20)\b", text)
        parts = [f"RSI {period.group(1)}" if period else "", f"{levels.group(1)}/{levels.group(2)}" if levels else ""]
        return " · ".join(p for p in parts if p)
    return ""


def _confluences(text: str, entries: list[str], plan: PlanContext) -> list[dict]:
    library = {name.lower(): name for name in plan.confluences}
    hits: list[tuple[int, str, re.Pattern]] = []
    for name, pattern in _COMPILED_CONFLUENCES:
        match = pattern.search(text)
        if match:
            hits.append((match.start(), library.get(name.lower(), name), pattern))
    for name in plan.confluences:
        match = re.search(rf"(?<!\w){re.escape(name)}(?!\w)", text, _FLAGS)
        if match and all(existing.lower() != name.lower() for _, existing, _p in hits):
            hits.append((match.start(), name, re.compile(re.escape(name), _FLAGS)))
    out = []
    for _, name, pattern in sorted(hits, key=lambda hit: hit[0]):
        in_entry = any(pattern.search(rule) for rule in entries)
        must = any(MUST_RE.search(s) and pattern.search(s) for s in _sentences(text) if len(s) <= _LONG_SENTENCE)
        out.append({"name": name, "required": in_entry or must, "detail": _detail(name, text)})
    return out[:MAX_CONFLUENCES]


def _entries(text: str) -> list[str]:
    """Entry triggers, plus the conditions the text says the setup must meet ("the H4 trend must be bullish")."""
    triggers = _snippets(text, ENTRY_RE, 8)
    conditions = [
        rule
        for rule in _snippets(text, MUST_RE, 6)
        if not (STOP_RE.search(rule) or EXIT_RE.search(rule) or FILTER_RE.search(rule)) or ENTRY_RE.search(rule)
    ]
    order = {sentence: index for index, sentence in enumerate(_sentences(text))}
    combined = list(dict.fromkeys(triggers + conditions))
    combined.sort(key=lambda rule: order.get(rule, len(order)))
    return combined[:8]


def _name(text: str, draft: dict) -> str:
    for pattern in NAME_RE:
        match = pattern.search(text)
        if match:
            return _text(match.group(1), NAME_MAX)
    parts = [*draft["markets"][:1], *draft["timeframes"][:1]]
    if draft["confluences"]:
        parts.append(draft["confluences"][0]["name"])
    return _text(" ".join(parts), NAME_MAX)


def _summary(text: str) -> str:
    sentences = [s for s in _sentences(text) if len(s) <= _LONG_SENTENCE and not NAME_RE[0].match(s)]
    if sentences:
        summary = ""
        for sentence in sentences[:3]:
            if len(summary) + len(sentence) > 280:
                break
            summary = f"{summary} {sentence}".strip()
        if summary:
            return summary
    words = text.split()  # a caption without punctuation: start where the strategy is named, past the greetings
    start = next(
        (i for i, word in enumerate(words) if re.match(r"(?:strateg|setup|system|sistem|metod)", word, _FLAGS)), 0
    )
    start = max(0, start - 6)
    return ("… " if start else "") + " ".join(words[start : start + 40]) + (" …" if len(words) > start + 40 else "")


MISSING = (
    ("entry_rules", N_("The entry trigger: what exactly makes you enter")),
    ("stop_loss", N_("Where the stop loss goes")),
    ("exit_rules", N_("How profits are taken: targets, partials, trailing")),
    ("timeframes", N_("The timeframes it is traded on")),
    ("markets", N_("The markets it is traded on")),
)


def missing_items(draft: StrategyDraft) -> list[str]:
    out = [tr(text) for key, text in MISSING if not getattr(draft, key)]
    if draft.risk.risk_per_trade_pct is None:
        out.append(tr("The risk per trade"))
    return out


_NEGATION = re.compile(
    r"\b(?:no|not|never|nothing|nobody|isn'?t|aren'?t|don'?t|doesn'?t|won'?t|avoid\w*|non|mai|senza|niente|nulla|"
    r"nessun\w*|evita\w*)\b",
    _FLAGS,
)


def _stated(pattern: re.Pattern, text: str) -> bool:
    """Whether the text says it, not that it does not ("I never use martingale", "non uso la martingala")."""
    for match in pattern.finditer(text):
        before = text[max(0, match.start() - 30) : match.start()]
        if not _NEGATION.search(before.split(".")[-1]):
            return True
    return False


def text_warnings(text: str) -> list[str]:
    """Red flags stated in the source text itself."""
    out = []
    if _stated(NO_STOP_RE, text):
        out.append(
            tr(
                "The source trades without a stop loss. Trading OS needs a stop on every trade: define one before "
                "backtesting."
            )
        )
    if _stated(MARTINGALE_RE, text):
        out.append(tr("Martingale or averaging down multiplies losses: your guardrails block oversized trades."))
    if _stated(GUARANTEE_RE, text):
        out.append(tr("The source promises guaranteed or risk-free results. No strategy is: prove the edge first."))
    return out


def plan_warnings(draft: StrategyDraft, plan: PlanContext) -> list[str]:
    """Where the draft goes beyond the trader's own plan (computed here, whatever the engine said)."""
    out = []
    risk = draft.risk
    if risk.risk_per_trade_pct is not None and plan.max_risk_pct and risk.risk_per_trade_pct > plan.max_risk_pct:
        out.append(
            tr(
                "The source risks {risk:g}% per trade, above your plan's {cap:g}% maximum: your guardrails still "
                "cap every trade.",
                risk=risk.risk_per_trade_pct,
                cap=plan.max_risk_pct,
            )
        )
    if risk.min_rr is not None and plan.min_rr and risk.min_rr < plan.min_rr:
        out.append(
            tr(
                "It aims for 1:{rr:g}, below your plan's 1:{minimum:g} minimum: those trades would be flagged or "
                "blocked.",
                rr=risk.min_rr,
                minimum=plan.min_rr,
            )
        )
    if (
        risk.max_daily_loss_pct is not None
        and plan.max_daily_loss_pct
        and risk.max_daily_loss_pct > plan.max_daily_loss_pct
    ):
        out.append(
            tr(
                "Its daily loss limit ({loss:g}%) is looser than your plan's {cap:g}%: your plan's limit applies.",
                loss=risk.max_daily_loss_pct,
                cap=plan.max_daily_loss_pct,
            )
        )
    return out


def with_checks(draft: StrategyDraft, source_text: str) -> StrategyDraft:
    """The draft with the source's own red flags added once (the checks against the trader's plan limits are not
    stored: ``plan_warnings`` computes them from the current values whenever the draft is shown or used)."""
    warnings = list(draft.warnings)
    for warning in text_warnings(source_text):
        if warning.lower() not in {w.lower() for w in warnings}:
            warnings.append(warning)
    return draft.model_copy(update={"warnings": warnings[:MAX_NOTES]})


def refresh_missing(draft: StrategyDraft) -> StrategyDraft:
    """Missing items after an edit: the standard ones follow the fields, any other note (from Claude) stays."""
    standard = {tr(text) for _key, text in MISSING} | {tr("The risk per trade")}
    kept = [item for item in draft.missing if item not in standard]
    return draft.model_copy(update={"missing": (kept + missing_items(draft))[:MAX_NOTES]})


def basic_extract(text: str, plan: PlanContext) -> StrategyDraft:
    """A draft from ``text`` without AI (English or Italian)."""
    entries = _entries(text)
    stops = _snippets(text, STOP_RE, 4)
    # The stop rule: a sentence about the stop that is not mainly about taking profit ("move the stop to BE").
    stop = next((rule for rule in stops if not EXIT_RE.search(rule)), stops[0] if stops else "")
    exits = [rule for rule in _snippets(text, EXIT_RE, 8) if rule != stop][:6]
    draft: dict[str, Any] = {
        "markets": _markets(text, plan),
        "timeframes": _timeframes_in(text),
        "sessions": _sessions(text, plan),
        "direction": "long" if LONG_ONLY_RE.search(text) else "short" if SHORT_ONLY_RE.search(text) else "both",
        "entry_rules": entries,
        "exit_rules": exits,
        "stop_loss": stop,
        "filters": _snippets(text, FILTER_RE, 5),
        "risk": {
            "risk_per_trade_pct": _first_number(RISK_PCT_RE, text, 0, 100),
            "min_rr": _first_number(RR_RE, text, 0, RR_EXTRACT_MAX),
            "max_trades_per_day": _trades_per_day(text),
            "max_daily_loss_pct": _first_number((DAILY_LOSS_RE,), text, 0, 100),
        },
    }
    draft["confluences"] = _confluences(text, entries, plan)
    draft["name"] = _name(text, draft)
    draft["summary"] = _summary(text)
    result = StrategyDraft.model_validate(draft)
    return with_checks(result.model_copy(update={"missing": missing_items(result)}), text)


def _trades_per_day(text: str) -> int | None:
    match = TRADES_RE.search(text)
    if not match:
        return None
    word = match.group(1).lower()
    value = int(word) if word.isdigit() else _COUNT_WORDS.get(word)
    return value if value and 1 <= value <= 100 else None


def merge(current: StrategyDraft, extra: StrategyDraft, plan: PlanContext, text: str) -> StrategyDraft:
    """The basic engine's follow-up: what a new message adds, without dropping anything the trader kept."""
    data = current.model_dump()
    new = extra.model_dump()
    for key in ("markets", "timeframes", "sessions", "entry_rules", "exit_rules", "filters"):
        data[key] = data[key] + [item for item in new[key] if item not in data[key]]
    names = {c["name"].lower() for c in data["confluences"]}
    data["confluences"] += [c for c in new["confluences"] if c["name"].lower() not in names]
    for key in ("stop_loss",):
        data[key] = new[key] or data[key]
    if new["direction"] != "both":
        data["direction"] = new["direction"]
    for key, value in new["risk"].items():
        if key != "notes" and value is not None:
            data["risk"][key] = value
    if any(pattern.search(text) for pattern in NAME_RE) and new["name"]:
        data["name"] = new["name"]
    merged = StrategyDraft.model_validate(data)
    return with_checks(refresh_missing(merged), text)


def basic_reply(draft: StrategyDraft, *, follow_up: bool, ai_configured: bool) -> str:
    """What the basic engine tells the trader (in the current language)."""
    parts = [
        (
            tr(
                "Basic extraction (no AI): {entries} entry rules, {exits} exit rules, {confluences} confluences and "
                "{timeframes} timeframes so far.",
                entries=len(draft.entry_rules),
                exits=len(draft.exit_rules),
                confluences=len(draft.confluences),
                timeframes=len(draft.timeframes),
            )
            if not follow_up
            else tr(
                "Added what I recognised to the draft. Basic extraction only adds: remove or reword items directly in "
                "the draft."
            )
        )
    ]
    if draft.missing:
        parts.append(tr("Most important gap: {gap}.", gap=draft.missing[0].rstrip(".")))
    parts.append(tr("Check every rule in the draft: I copy sentences from the text and can pick up the wrong ones."))
    if not ai_configured:
        parts.append(tr("Set up Claude in Trading plan -> AI assistant for a full reading of the text."))
    return " ".join(parts)
