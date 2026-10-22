"""Vision AI: chart analysis with Claude, follow-up Q&A and the AI assistant settings."""

from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, File, Form, HTTPException, Query, Response, UploadFile
from fastapi.responses import JSONResponse
from pydantic import ConfigDict, StringConstraints, field_validator, model_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import AppConfig
from ..constants import AI_EFFORTS, AI_MODELS, TIMEFRAMES
from ..models import AccountSettings, Strategy, VisionAnalysis
from ..schemas import FiniteModel
from ..services import ai_client, uploads, vision
from ..services.account import local_now
from .deps import ConfigDep, DbDep, SettingsDep, get_or_404

router = APIRouter(prefix="/api", tags=["vision"])

SUMMARY_PREVIEW_CHARS = 240


class VisionMessageOut(FiniteModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    role: str
    text: str
    created_at: datetime


class VisionSummaryOut(FiniteModel):
    id: int
    created_at: datetime
    symbol: str
    timeframe: str
    strategy: str
    status: str
    model: str
    error: str
    image_src: str
    summary: str
    trend: str | None


class VisionAnalysisOut(VisionSummaryOut):
    user_notes: str
    image_width: int | None
    image_height: int | None
    analysis: dict[str, Any] | None
    overlay: dict[str, list[dict[str, Any]]] | None
    fomo: dict[str, Any] | None = None  # anti-FOMO findings (None when the check is off or nothing was analysed)
    messages: list[VisionMessageOut]


class QuestionIn(FiniteModel):
    question: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2000)]


class QuestionOut(FiniteModel):
    question: VisionMessageOut
    answer: VisionMessageOut


class AISettingsOut(FiniteModel):
    model: str
    effort: str
    models: list[dict[str, Any]]
    efforts: list[dict[str, str]]
    key_source: str
    package_installed: bool


class AISettingsIn(FiniteModel):
    model: str | None = None
    effort: str | None = None
    api_key: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)] | None = None
    clear_key: bool = False

    @field_validator("model")
    @classmethod
    def _known_model(cls, value: str | None) -> str | None:
        if value is not None and value not in AI_MODELS:
            raise ValueError(f"Unknown model - choose one of {', '.join(AI_MODELS)}")
        return value

    @field_validator("effort")
    @classmethod
    def _known_effort(cls, value: str | None) -> str | None:
        if value is not None and value not in AI_EFFORTS:
            raise ValueError(f"Unknown effort - choose one of {', '.join(AI_EFFORTS)}")
        return value

    @field_validator("api_key")
    @classmethod
    def _plausible_key(cls, value: str | None) -> str | None:
        if value is not None and any(ch.isspace() for ch in value):
            raise ValueError("The API key must not contain spaces")
        return value

    @model_validator(mode="after")
    def _one_key_action(self) -> "AISettingsIn":
        if self.api_key and self.clear_key:
            raise ValueError("Either save a new API key or remove the saved one, not both")
        return self


# ------------------------------------------------------------------- helpers


def _summary(record: VisionAnalysis) -> dict[str, Any]:
    analysis = record.analysis or {}
    return {
        "id": record.id,
        "created_at": record.created_at,
        "symbol": record.symbol,
        "timeframe": record.timeframe,
        "strategy": record.strategy,
        "status": record.status,
        "model": record.model,
        "error": record.error,
        "image_src": f"/uploads/{record.filename}",
        "summary": (analysis.get("summary") or "")[:SUMMARY_PREVIEW_CHARS],
        "trend": (analysis.get("trend") or {}).get("direction"),
    }


def _fomo(db: Session, settings: AccountSettings, record: VisionAnalysis) -> dict[str, Any] | None:
    """Anti-FOMO findings for the stored analysis, judged now against the account's and strategy's minimum R:R."""
    if not record.analysis or not settings.fomo_guard:
        return None
    strategy = db.scalar(select(Strategy).where(Strategy.name == record.strategy)) if record.strategy else None
    min_rr = max(settings.min_rr or 0.0, (strategy.min_rr if strategy else 0.0) or 0.0)
    return vision.fomo_check(record.analysis, min_rr)


def _full(record: VisionAnalysis, db: Session, settings: AccountSettings) -> dict[str, Any]:
    has_overlay = bool(record.analysis and record.image_width and record.image_height)
    return {
        **_summary(record),
        "user_notes": record.user_notes,
        "image_width": record.image_width,
        "image_height": record.image_height,
        "analysis": record.analysis,
        "overlay": vision.overlay(record.analysis, record.image_width, record.image_height) if has_overlay else None,
        "fomo": _fomo(db, settings, record),
        "messages": record.messages,
    }


def _unavailable(exc: ai_client.AIUnavailable, analysis_id: int | None = None) -> JSONResponse:
    content: dict[str, Any] = {"detail": str(exc), "setup": list(ai_client.SETUP_STEPS)}
    if analysis_id is not None:
        content["analysis_id"] = analysis_id
    return JSONResponse(status_code=503, content=content)


def _ai_settings(settings: AccountSettings) -> dict[str, Any]:
    return {
        "model": settings.ai_model,
        "effort": settings.ai_effort,
        "models": ai_client.models_catalog(),
        "efforts": ai_client.efforts_catalog(),
        "key_source": ai_client.key_source(settings),
        "package_installed": ai_client.package_installed(),
    }


def _run(db: Session, settings: AccountSettings, config: AppConfig, record: VisionAnalysis, client: Any) -> Any:
    try:
        vision.run_analysis(db, settings, config.uploads_dir, record, client)
    except vision.VisionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ai_client.AIUnavailable as exc:
        return _unavailable(exc, record.id)
    except ai_client.AIRequestError as exc:
        return JSONResponse(status_code=exc.status_code, content={"detail": str(exc), "analysis_id": record.id})
    return _full(record, db, settings)


# ----------------------------------------------------------------- analyses


@router.post("/vision/analyses", response_model=VisionAnalysisOut, status_code=201)
def create_analysis(
    db: DbDep,
    settings: SettingsDep,
    config: ConfigDep,
    file: Annotated[UploadFile, File()],
    symbol: Annotated[str, Form(max_length=20)] = "",
    timeframe: Annotated[str, Form(max_length=10)] = "",
    strategy: Annotated[str, Form(max_length=60)] = "",
    notes: Annotated[str, Form(max_length=2000)] = "",
):
    """Upload a chart screenshot and analyse it synchronously (can take a minute)."""
    timeframe = timeframe.strip()
    if timeframe and timeframe not in TIMEFRAMES:
        raise HTTPException(status_code=400, detail=f"Unknown timeframe - choose one of {', '.join(TIMEFRAMES)}")
    data = file.file.read(vision.MAX_IMAGE_BYTES + 1)
    try:
        _, width, height = vision.image_info(data)
    except vision.VisionError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    try:
        client = ai_client.get_client(settings)
    except ai_client.AIUnavailable as exc:
        return _unavailable(exc)
    record = VisionAnalysis(
        filename=uploads.save_image(config.uploads_dir, data, vision.MAX_IMAGE_BYTES),
        image_width=width,
        image_height=height,
        symbol=symbol.strip().upper(),
        timeframe=timeframe,
        strategy=strategy.strip(),
        user_notes=notes.strip(),
        status="pending",
        model=settings.ai_model,
        created_at=local_now(settings),
    )
    db.add(record)
    db.commit()
    return _run(db, settings, config, record, client)


@router.get("/vision/analyses", response_model=list[VisionSummaryOut])
def list_analyses(db: DbDep, limit: int = Query(50, ge=1, le=200)):
    rows = db.scalars(select(VisionAnalysis).order_by(VisionAnalysis.id.desc()).limit(limit))
    return [_summary(row) for row in rows]


@router.get("/vision/analyses/{analysis_id}", response_model=VisionAnalysisOut)
def read_analysis(analysis_id: int, db: DbDep, settings: SettingsDep):
    return _full(get_or_404(db, VisionAnalysis, analysis_id), db, settings)


@router.delete("/vision/analyses/{analysis_id}", status_code=204)
def delete_analysis(analysis_id: int, db: DbDep, config: ConfigDep):
    record = get_or_404(db, VisionAnalysis, analysis_id)
    uploads.delete_file(config.uploads_dir, record.filename)
    db.delete(record)
    db.commit()
    return Response(status_code=204)


@router.post("/vision/analyses/{analysis_id}/retry", response_model=VisionAnalysisOut)
def retry_analysis(analysis_id: int, db: DbDep, settings: SettingsDep, config: ConfigDep):
    """Run the analysis again with the current model settings (clears earlier Q&A)."""
    record = get_or_404(db, VisionAnalysis, analysis_id)
    try:
        client = ai_client.get_client(settings)
    except ai_client.AIUnavailable as exc:
        return _unavailable(exc, record.id)
    return _run(db, settings, config, record, client)


@router.post("/vision/analyses/{analysis_id}/messages", response_model=QuestionOut, status_code=201)
def ask_question(analysis_id: int, payload: QuestionIn, db: DbDep, settings: SettingsDep, config: ConfigDep):
    """Ask a follow-up question about an analysed chart."""
    record = get_or_404(db, VisionAnalysis, analysis_id)
    try:
        client = ai_client.get_client(settings)
        asked, answered = vision.ask(
            db, settings, config.uploads_dir, record, payload.question, client, local_now(settings)
        )
    except ai_client.AIUnavailable as exc:
        return _unavailable(exc, record.id)
    except ai_client.AIRequestError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    except vision.VisionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except vision.AnalysisFailed as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"question": asked, "answer": answered}


# ----------------------------------------------------------------- settings


@router.get("/ai/settings", response_model=AISettingsOut)
def read_ai_settings(settings: SettingsDep):
    return _ai_settings(settings)


@router.put("/ai/settings", response_model=AISettingsOut)
def update_ai_settings(payload: AISettingsIn, settings: SettingsDep, db: DbDep):
    """Change model / effort and save or remove the local API key (never echoed back)."""
    if payload.model is not None:
        settings.ai_model = payload.model
    if payload.effort is not None:
        settings.ai_effort = payload.effort
    if payload.api_key:
        settings.ai_api_key = payload.api_key
    elif payload.clear_key:
        settings.ai_api_key = ""
    db.commit()
    return _ai_settings(settings)
