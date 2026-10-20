"""Vision AI: chart screenshot analysis and follow-up Q&A with Claude.

Pure helpers (image header parsing, the structured-output schema, prompt and request
builders, overlay normalisation) plus the two database workflows ``run_analysis`` and
``ask``. Images are read from the uploads folder for every request and are never stored
as base64 in the database.

Coordinates: Claude reports pixel positions in the image *as it sees it*, i.e. after the
API's own downscale to the high-resolution tier limits (long edge 2576 px, 4784 visual
tokens - every model in ``AI_MODELS`` is on that tier). The visible size is computed with
``resized_size`` and sent in the prompt, and overlay positions are normalised by it.
"""

from __future__ import annotations

import base64
import json
import math
from datetime import datetime
from pathlib import Path
from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..constants import AI_FALLBACK_MODELS
from ..models import AccountSettings, Strategy, VisionAnalysis, VisionMessage
from . import ai_client
from .uploads import detect_image_type

# 5 MB raw keeps the base64 payload (~6.7 MB) well under the Claude API's 10 MB per-image limit.
MAX_IMAGE_BYTES = 5 * 1024 * 1024
MAX_IMAGE_EDGE = 8000  # API hard limit per side
VISION_MAX_EDGE = 2576
VISION_MAX_TOKENS = 4784
PATCH_PX = 28

ANALYSIS_MAX_TOKENS = 32000
QA_MAX_TOKENS = 16000
# Follow-up answers reason over an existing analysis, so chat runs at most at "medium" effort
# (faster, cheaper); a lower saved effort is respected.
QA_EFFORT_CEILING = "medium"
FALLBACK_BETA = "server-side-fallback-2026-07-01"

MEDIA_TYPES = {"png": "image/png", "jpg": "image/jpeg", "gif": "image/gif", "webp": "image/webp"}


class VisionError(ValueError):
    """Invalid input (unsupported or damaged image, impossible request)."""


class AnalysisFailed(RuntimeError):
    """The model answered but the result is unusable (refusal, truncation, bad format)."""


# --------------------------------------------------------------------------- images


def _png_size(data: bytes) -> tuple[int, int] | None:
    if len(data) < 24 or data[12:16] != b"IHDR":
        return None
    return int.from_bytes(data[16:20], "big"), int.from_bytes(data[20:24], "big")


def _gif_size(data: bytes) -> tuple[int, int] | None:
    if len(data) < 10:
        return None
    return int.from_bytes(data[6:8], "little"), int.from_bytes(data[8:10], "little")


def _webp_size(data: bytes) -> tuple[int, int] | None:
    chunk = data[12:16]
    if chunk == b"VP8 " and len(data) >= 30 and data[23:26] == b"\x9d\x01\x2a":
        return int.from_bytes(data[26:28], "little") & 0x3FFF, int.from_bytes(data[28:30], "little") & 0x3FFF
    if chunk == b"VP8L" and len(data) >= 25 and data[20] == 0x2F:
        bits = int.from_bytes(data[21:25], "little")
        return (bits & 0x3FFF) + 1, ((bits >> 14) & 0x3FFF) + 1
    if chunk == b"VP8X" and len(data) >= 30:
        return int.from_bytes(data[24:27], "little") + 1, int.from_bytes(data[27:30], "little") + 1
    return None


def _exif_orientation(segment: bytes) -> int:
    """EXIF orientation tag (1-8) from an APP1 payload; 1 when absent."""
    if not segment.startswith(b"Exif\x00\x00"):
        return 1
    tiff = segment[6:]
    order: Literal["little", "big"] = "little" if tiff[:2] == b"II" else "big"
    if len(tiff) < 8:
        return 1
    ifd = int.from_bytes(tiff[4:8], order)
    if ifd + 2 > len(tiff):
        return 1
    for index in range(int.from_bytes(tiff[ifd : ifd + 2], order)):
        entry = tiff[ifd + 2 + index * 12 : ifd + 14 + index * 12]
        if len(entry) < 12:
            break
        if int.from_bytes(entry[0:2], order) == 0x0112:
            return int.from_bytes(entry[8:10], order)
    return 1


def _jpeg_size(data: bytes) -> tuple[int, int] | None:
    """Frame size from the SOF marker, swapped when EXIF says the image is displayed rotated."""
    orientation = 1
    pos = 2
    while pos + 4 <= len(data):
        if data[pos] != 0xFF:
            return None
        marker = data[pos + 1]
        if marker == 0xFF:  # fill byte
            pos += 1
            continue
        if marker == 0x01 or 0xD0 <= marker <= 0xD8:  # standalone markers
            pos += 2
            continue
        if marker in (0xD9, 0xDA):  # end of image / start of scan before any frame header
            return None
        length = int.from_bytes(data[pos + 2 : pos + 4], "big")
        if marker == 0xE1:
            orientation = _exif_orientation(data[pos + 4 : pos + 2 + length])
        elif 0xC0 <= marker <= 0xCF and marker not in (0xC4, 0xC8, 0xCC):
            if pos + 9 > len(data):
                return None
            height = int.from_bytes(data[pos + 5 : pos + 7], "big")
            width = int.from_bytes(data[pos + 7 : pos + 9], "big")
            return (height, width) if orientation in (5, 6, 7, 8) else (width, height)
        pos += 2 + length
    return None


_SIZE_READERS = {"png": _png_size, "jpg": _jpeg_size, "gif": _gif_size, "webp": _webp_size}


def image_info(data: bytes) -> tuple[str, int, int]:
    """Validate a chart image and return ``(kind, width, height)`` from its header."""
    if not data:
        raise VisionError("The uploaded file is empty")
    if len(data) > MAX_IMAGE_BYTES:
        raise VisionError(f"Chart images must be {MAX_IMAGE_BYTES // (1024 * 1024)} MB or smaller")
    kind = detect_image_type(data[:16])
    if kind is None:
        raise VisionError("Only PNG, JPEG, GIF and WebP images are supported")
    size = _SIZE_READERS[kind](data)
    if size is None or min(size) < 1:
        raise VisionError("Could not read the image dimensions - the file may be damaged")
    width, height = size
    if max(width, height) > MAX_IMAGE_EDGE:
        raise VisionError(f"Images can be at most {MAX_IMAGE_EDGE} px on each side (this one is {width}x{height})")
    return kind, width, height


def resized_size(width: int, height: int) -> tuple[int, int]:
    """The size Claude downscales an image to before reading it (Anthropic's reference rule)."""

    def fits(w: int, h: int) -> bool:
        return (
            math.ceil(w / PATCH_PX) * PATCH_PX <= VISION_MAX_EDGE
            and math.ceil(h / PATCH_PX) * PATCH_PX <= VISION_MAX_EDGE
            and math.ceil(w / PATCH_PX) * math.ceil(h / PATCH_PX) <= VISION_MAX_TOKENS
        )

    if fits(width, height):
        return width, height
    if height > width:
        resized_h, resized_w = resized_size(height, width)
        return resized_w, resized_h
    aspect = width / height
    lo, hi = 1, width  # lo always fits; hi never does
    while lo + 1 < hi:
        mid = (lo + hi) // 2
        if fits(mid, max(round(mid / aspect), 1)):
            lo = mid
        else:
            hi = mid
    return lo, max(round(lo / aspect), 1)


def image_block(kind: str, data: bytes) -> dict:
    return {
        "type": "image",
        "source": {"type": "base64", "media_type": MEDIA_TYPES[kind], "data": base64.standard_b64encode(data).decode()},
    }


# --------------------------------------------------------------------------- analysis schema

TREND_DIRECTIONS = ("bullish", "bearish", "ranging", "unclear")
STRENGTHS = ("strong", "moderate", "weak")
CONFIDENCES = ("high", "medium", "low")
LEVEL_KINDS = ("support", "resistance", "pivot", "round_number", "previous_high", "previous_low", "other")
ZONE_KINDS = (
    "demand",
    "supply",
    "buy_side_liquidity",
    "sell_side_liquidity",
    "order_block",
    "fair_value_gap",
    "consolidation",
    "imbalance",
    "other",
)
PATTERN_CATEGORIES = ("candlestick", "chart", "structure")
BIASES = ("bullish", "bearish", "neutral")
DIRECTIONS = ("long", "short")
ENTRY_TYPES = ("market", "limit", "stop")


def _obj(**properties: Any) -> dict:
    """Structured-output object: closed, with every property required."""
    return {"type": "object", "properties": properties, "required": list(properties), "additionalProperties": False}


def _enum(values: tuple[str, ...]) -> dict:
    return {"type": "string", "enum": list(values)}


def _nullable(schema: dict) -> dict:
    return {"anyOf": [schema, {"type": "null"}]}


def _array(items: dict) -> dict:
    return {"type": "array", "items": items}


_STR = {"type": "string"}
_NUM = {"type": "number"}
_OPT_NUM = _nullable(_NUM)
_OPT_STR = _nullable(_STR)

ANALYSIS_SCHEMA: dict = _obj(
    summary=_STR,
    trend=_obj(direction=_enum(TREND_DIRECTIONS), strength=_enum(STRENGTHS), description=_STR),
    price_axis=_obj(visible={"type": "boolean"}, min_price=_OPT_NUM, max_price=_OPT_NUM, current_price=_OPT_NUM),
    levels=_array(_obj(kind=_enum(LEVEL_KINDS), price=_OPT_NUM, y_px=_OPT_NUM, strength=_enum(STRENGTHS), note=_STR)),
    zones=_array(
        _obj(
            kind=_enum(ZONE_KINDS),
            price_top=_OPT_NUM,
            price_bottom=_OPT_NUM,
            y_top_px=_OPT_NUM,
            y_bottom_px=_OPT_NUM,
            x_start_px=_OPT_NUM,
            x_end_px=_OPT_NUM,
            note=_STR,
        )
    ),
    patterns=_array(
        _obj(
            name=_STR,
            category=_enum(PATTERN_CATEGORIES),
            bias=_enum(BIASES),
            confidence=_enum(CONFIDENCES),
            x_px=_OPT_NUM,
            y_px=_OPT_NUM,
            note=_STR,
        )
    ),
    structure=_obj(description=_STR, last_bos=_OPT_STR, last_choch=_OPT_STR),
    trade_ideas=_array(
        _obj(
            direction=_enum(DIRECTIONS),
            entry_type=_enum(ENTRY_TYPES),
            entry=_OPT_NUM,
            stop_loss=_OPT_NUM,
            take_profits=_array(_NUM),
            trigger=_STR,
            rationale=_STR,
            invalidation=_STR,
            confidence=_enum(CONFIDENCES),
        )
    ),
    strategy_fit=_obj(strategy=_OPT_STR, fits=_nullable({"type": "boolean"}), missing=_array(_STR), notes=_STR),
    risks=_array(_STR),
    caveats=_array(_STR),
    symbol_guess=_OPT_STR,
    timeframe_guess=_OPT_STR,
)

Num = Annotated[float, Field(allow_inf_nan=False)]


class Trend(BaseModel):
    direction: Literal[TREND_DIRECTIONS]
    strength: Literal[STRENGTHS]
    description: str


class PriceAxis(BaseModel):
    visible: bool
    min_price: Num | None
    max_price: Num | None
    current_price: Num | None


class Level(BaseModel):
    kind: Literal[LEVEL_KINDS]
    price: Num | None
    y_px: Num | None
    strength: Literal[STRENGTHS]
    note: str


class Zone(BaseModel):
    kind: Literal[ZONE_KINDS]
    price_top: Num | None
    price_bottom: Num | None
    y_top_px: Num | None
    y_bottom_px: Num | None
    x_start_px: Num | None
    x_end_px: Num | None
    note: str


class Pattern(BaseModel):
    name: str
    category: Literal[PATTERN_CATEGORIES]
    bias: Literal[BIASES]
    confidence: Literal[CONFIDENCES]
    x_px: Num | None
    y_px: Num | None
    note: str


class Structure(BaseModel):
    description: str
    last_bos: str | None
    last_choch: str | None


class TradeIdea(BaseModel):
    direction: Literal[DIRECTIONS]
    entry_type: Literal[ENTRY_TYPES]
    entry: Num | None
    stop_loss: Num | None
    take_profits: list[Num]
    trigger: str
    rationale: str
    invalidation: str
    confidence: Literal[CONFIDENCES]


class StrategyFit(BaseModel):
    strategy: str | None
    fits: bool | None
    missing: list[str]
    notes: str


class ChartAnalysis(BaseModel):
    summary: str
    trend: Trend
    price_axis: PriceAxis
    levels: list[Level]
    zones: list[Zone]
    patterns: list[Pattern]
    structure: Structure
    trade_ideas: list[TradeIdea]
    strategy_fit: StrategyFit
    risks: list[str]
    caveats: list[str]
    symbol_guess: str | None
    timeframe_guess: str | None


# --------------------------------------------------------------------------- prompts

# Frozen: this is the prompt-cache prefix, so it must never contain per-request values.
SYSTEM_PROMPT = """You are an objective technical analyst. You read screenshots of trading charts for a \
discretionary trader who keeps a trading journal.

How to analyse a chart:
- Report every key technical element visible on the chart, whether or not it fits the trader's strategy: \
support and resistance levels, the trend, liquidity (buy-side and sell-side liquidity, equal highs and lows), \
supply and demand zones, order blocks, fair value gaps and other imbalances, consolidation ranges, candlestick \
patterns, chart patterns and market structure (swing highs and lows, breaks of structure, changes of character). \
The trader's strategy only informs the strategy_fit section; it must never filter or bias the rest of the analysis.
- Read prices from the price axis only where the labels are legible. When a price cannot be read with \
confidence, use null - never invent prices, symbols or timeframes.
- Give positions as pixel coordinates in the image exactly as you see it: origin at the top-left corner, x grows \
to the right, y grows downward. A horizontal level gets the y of its line; a zone gets its top and bottom y and, \
when it is bounded in time, its left and right x; a pattern gets the x and y of its most characteristic candle \
or point. Use null when an element cannot be located.
- Derive potential trade ideas from structure: an entry, a stop loss beyond the structure that invalidates the \
idea, and targets at the next levels or liquidity. Each idea needs a concrete trigger and invalidation. Offer \
ideas in both directions when the chart supports them, and none when it shows no clear setup.
- State uncertainty honestly: use the confidence and strength fields, list caveats (for example low resolution, \
cropped history, an unreadable axis, no volume) and keep the risks specific to this chart.
- Be concise and concrete. This is educational analysis, not financial advice; never tell the trader what they \
must do.

When the trader asks follow-up questions about a chart you analysed, answer in plain text paragraphs (simple \
"- " bullet lines are fine; no Markdown headings, bold text or tables). Quote levels by price when known, stay \
consistent with your analysis or say clearly why you revise it, and keep answers focused."""

QA_INSTRUCTIONS = (
    "Above is your earlier structured analysis of this chart. Answer the trader's follow-up questions about it."
)


def _strategy_details(strategy: Strategy | None) -> list[str]:
    if strategy is None:
        return []
    lines = [f"Strategy details for {strategy.name}:"]
    if strategy.description:
        lines.append(f"- Description: {strategy.description}")
    if strategy.rules:
        lines.append(f"- Rules: {strategy.rules}")
    if strategy.allowed_timeframes:
        lines.append(f"- Allowed timeframes: {', '.join(strategy.allowed_timeframes)}")
    if strategy.allowed_directions != "both":
        lines.append(f"- Direction: {strategy.allowed_directions} only")
    if strategy.required_confluences:
        lines.append(f"- Required confluences: {', '.join(strategy.required_confluences)}")
    if strategy.min_rr:
        lines.append(f"- Minimum reward:risk: {strategy.min_rr:g}")
    return lines


def chart_context(record: VisionAnalysis) -> str:
    """Deterministic description of the chart built only from immutable record fields."""
    width, height = resized_size(record.image_width or 1, record.image_height or 1)
    lines = [
        f"Chart image: {width}x{height} pixels as you see it. Report pixel coordinates in this image.",
        f"Instrument (from the trader): {record.symbol or 'not given - identify it from the chart if it is shown'}",
        f"Timeframe (from the trader): {record.timeframe or 'not given - identify it from the chart if it is shown'}",
        f"Trader's strategy: {record.strategy or 'none selected - set strategy_fit.strategy and fits to null'}",
    ]
    if record.user_notes:
        lines.append(f"Trader's notes: {record.user_notes}")
    return "\n".join(lines)


def analysis_request(
    settings: AccountSettings, record: VisionAnalysis, image: dict, strategy: Strategy | None
) -> dict[str, Any]:
    """Messages API parameters for the structured chart analysis."""
    text = "\n".join([chart_context(record), *_strategy_details(strategy), "", "Analyse this chart."])
    params: dict[str, Any] = {
        "model": settings.ai_model,
        "max_tokens": ANALYSIS_MAX_TOKENS,
        "thinking": {"type": "adaptive"},
        "output_config": {
            "effort": settings.ai_effort,
            "format": {"type": "json_schema", "schema": ANALYSIS_SCHEMA},
        },
        "system": [{"type": "text", "text": SYSTEM_PROMPT, "cache_control": {"type": "ephemeral"}}],
        "messages": [{"role": "user", "content": [image, {"type": "text", "text": text}]}],
    }
    return _with_fallbacks(params, settings.ai_model)


def conversation(
    record: VisionAnalysis, image: dict, history: list[VisionMessage], question: str
) -> list[dict[str, Any]]:
    """Q&A messages: a rebuilt (byte-identical) opening turn, then stored turns replayed unchanged."""
    questions = [m.text for m in history if m.role == "user"] + [question]
    answers = [m.content for m in history if m.role == "assistant"]
    opening = "\n\n".join(
        [
            chart_context(record),
            "Your analysis (JSON):\n" + json.dumps(record.analysis, sort_keys=True),
            QA_INSTRUCTIONS,
            f"Question: {questions[0]}",
        ]
    )
    messages: list[dict[str, Any]] = [{"role": "user", "content": [image, {"type": "text", "text": opening}]}]
    for answer, follow_up in zip(answers, questions[1:], strict=True):
        messages.append({"role": "assistant", "content": answer})
        messages.append({"role": "user", "content": [{"type": "text", "text": follow_up}]})
    return messages


def qa_request(settings: AccountSettings, messages: list[dict[str, Any]]) -> dict[str, Any]:
    """Messages API parameters for a follow-up question (plain-text answer, automatic caching)."""
    params: dict[str, Any] = {
        "model": settings.ai_model,
        "max_tokens": QA_MAX_TOKENS,
        "thinking": {"type": "adaptive"},
        "output_config": {"effort": ai_client.capped_effort(settings.ai_effort, QA_EFFORT_CEILING)},
        "system": [{"type": "text", "text": SYSTEM_PROMPT, "cache_control": {"type": "ephemeral"}}],
        "cache_control": {"type": "ephemeral"},
        "messages": messages,
    }
    return _with_fallbacks(params, settings.ai_model)


def _with_fallbacks(params: dict[str, Any], model: str) -> dict[str, Any]:
    """Server-side refusal fallback for the models that support it."""
    if model in AI_FALLBACK_MODELS:
        params["betas"] = [FALLBACK_BETA]
        params["fallbacks"] = "default"
    return params


# --------------------------------------------------------------------------- responses


def _check_stop(final: Any, refusal: str, truncated: str) -> None:
    if final.stop_reason == "refusal":
        raise AnalysisFailed(refusal)
    if final.stop_reason == "max_tokens":
        raise AnalysisFailed(truncated)


def _texts(final: Any) -> list[str]:
    return [block.text for block in final.content if block.type == "text"]


def parse_analysis(final: Any) -> dict:
    """Validate the structured analysis in a final message; raises :class:`AnalysisFailed`."""
    _check_stop(
        final,
        "The model declined to analyse this image.",
        "The analysis was cut off before it finished. Try again, or lower the effort in Trading plan -> AI assistant.",
    )
    texts = _texts(final)
    try:
        return ChartAnalysis.model_validate(json.loads(texts[0])).model_dump(mode="json")
    except (IndexError, json.JSONDecodeError, ValidationError) as exc:
        raise AnalysisFailed("The model returned an analysis in an unexpected format. Try again.") from exc


def answer_text(final: Any) -> str:
    """Plain-text answer of a Q&A turn; raises :class:`AnalysisFailed`."""
    _check_stop(
        final, "The model declined to answer this question.", "The answer was cut off. Ask a narrower question."
    )
    text = "\n\n".join(t.strip() for t in _texts(final) if t.strip())
    if not text:
        raise AnalysisFailed("The model returned an empty answer. Try rephrasing the question.")
    return text


def served_model(final: Any, requested: str) -> str:
    """The model that produced the response (differs from the request after a refusal fallback)."""
    return getattr(final, "model", None) or requested


# --------------------------------------------------------------------------- overlay


def overlay(analysis: dict, width: int, height: int) -> dict[str, list[dict]]:
    """Normalise the analysis' pixel coordinates to 0..1 of the image for the SVG overlay.

    Items without usable coordinates are left out (the UI still lists them).
    """
    seen_w, seen_h = resized_size(width, height)

    def nx(value: float) -> float:
        return round(min(max(value / seen_w, 0.0), 1.0), 4)

    def ny(value: float) -> float:
        return round(min(max(value / seen_h, 0.0), 1.0), 4)

    levels = [{"index": i, "y": ny(lv["y_px"])} for i, lv in enumerate(analysis["levels"]) if lv["y_px"] is not None]
    zones = []
    for i, zone in enumerate(analysis["zones"]):
        if zone["y_top_px"] is None or zone["y_bottom_px"] is None:
            continue
        y0, y1 = sorted((ny(zone["y_top_px"]), ny(zone["y_bottom_px"])))
        x0 = nx(zone["x_start_px"]) if zone["x_start_px"] is not None else 0.0
        x1 = nx(zone["x_end_px"]) if zone["x_end_px"] is not None else 1.0
        x0, x1 = sorted((x0, x1))
        zones.append({"index": i, "x0": x0, "x1": x1, "y0": y0, "y1": y1})
    patterns = [
        {"index": i, "x": nx(p["x_px"]), "y": ny(p["y_px"])}
        for i, p in enumerate(analysis["patterns"])
        if p["x_px"] is not None and p["y_px"] is not None
    ]
    return {"levels": levels, "zones": zones, "patterns": patterns}


# --------------------------------------------------------------------------- workflows


def _load_image(uploads_dir: Path, record: VisionAnalysis) -> dict:
    try:
        data = (uploads_dir / record.filename).read_bytes()
    except FileNotFoundError as exc:
        raise VisionError("The chart image is missing from the uploads folder - delete this analysis") from exc
    kind, _, _ = image_info(data)
    return image_block(kind, data)


def run_analysis(
    db: Session, settings: AccountSettings, uploads_dir: Path, record: VisionAnalysis, client: Any
) -> VisionAnalysis:
    """(Re)run the analysis for ``record``; the outcome is stored on the record.

    Model-side failures (refusal, truncation, bad format) end as ``status="error"``. API
    errors are stored the same way and then re-raised for the HTTP layer. Earlier Q&A is
    removed because it refers to the previous analysis.
    """
    image = _load_image(uploads_dir, record)
    strategy = db.scalar(select(Strategy).where(Strategy.name == record.strategy)) if record.strategy else None
    record.messages.clear()
    record.status, record.error, record.analysis, record.model = "pending", "", None, settings.ai_model
    db.commit()
    try:
        final = ai_client.stream_final_message(client, analysis_request(settings, record, image, strategy))
        record.analysis = parse_analysis(final)
        record.status = "done"
        record.model = served_model(final, settings.ai_model)
    except AnalysisFailed as exc:
        record.status, record.error = "error", str(exc)
    except (ai_client.AIRequestError, ai_client.AIUnavailable) as exc:
        record.status, record.error = "error", str(exc)
        db.commit()
        raise
    db.commit()
    return record


def ask(
    db: Session,
    settings: AccountSettings,
    uploads_dir: Path,
    record: VisionAnalysis,
    question: str,
    client: Any,
    now: datetime,
) -> tuple[VisionMessage, VisionMessage]:
    """Ask a follow-up question; stores the question and answer only when the call succeeds."""
    if record.status != "done" or not record.analysis:
        raise VisionError("Questions can only be asked about a completed analysis")
    image = _load_image(uploads_dir, record)
    final = ai_client.stream_final_message(
        client, qa_request(settings, conversation(record, image, list(record.messages), question))
    )
    text = answer_text(final)
    asked = VisionMessage(role="user", text=question, content=[], created_at=now)
    answered = VisionMessage(role="assistant", text=text, content=final.to_dict()["content"], created_at=now)
    record.messages.extend([asked, answered])
    db.commit()
    return asked, answered
