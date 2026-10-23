"""AI Strategy Learner (v1.6): strategy descriptions and video transcripts -> incubator strategy drafts."""

from typing import Annotated, Any, Literal

from fastapi import APIRouter, HTTPException, Response
from fastapi.responses import JSONResponse
from pydantic import StringConstraints

from ..models import LearnerSession
from ..schemas import FiniteModel
from ..services import ai_client, learner
from ..services.account import local_now, local_today
from ..services.learner_extract import StrategyDraft, clean_transcript
from .deps import DbDep, SettingsDep, get_or_404

router = APIRouter(prefix="/api/learner", tags=["learner"])

Text = Annotated[str, StringConstraints(max_length=learner.TRANSCRIPT_RAW_MAX_CHARS)]


class MessageIn(FiniteModel):
    kind: Literal["rules", "transcript", "chat"] = "rules"
    text: Text
    engine: Literal["auto", "ai", "basic"] = "auto"


class CleanIn(FiniteModel):
    text: Text


class StrategyIn(FiniteModel):
    add_confluences: bool = True


def _client(settings):
    # Looked up on the module at call time so tests can replace ai_client.get_client.
    return ai_client.get_client(settings)


def _answer(call) -> Any:
    """Run a Learner call and map its failures to HTTP answers."""
    try:
        return call()
    except learner.LearnerError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except ai_client.AIUnavailable as exc:
        return JSONResponse(
            status_code=503, content={"detail": str(exc), "setup": ai_client.setup_steps(), "basic": True}
        )
    except ai_client.AIRequestError as exc:
        return JSONResponse(status_code=exc.status_code, content={"detail": str(exc), "basic": True})
    except learner.LearnerFailed as exc:
        return JSONResponse(status_code=422, content={"detail": str(exc), "basic": True})


@router.get("")
def overview(db: DbDep, settings: SettingsDep):
    """Which engine answers (Claude or the basic extractor) and the recent sessions."""
    return {
        "engine": learner.engine_info(settings),
        "sessions": learner.summaries(db),
        "limits": {
            "rules": learner.RULES_MAX_CHARS,
            "chat": learner.CHAT_MAX_CHARS,
            "transcript": learner.TRANSCRIPT_MAX_CHARS,
            "transcript_raw": learner.TRANSCRIPT_RAW_MAX_CHARS,
        },
    }


@router.post("/clean")
def clean(payload: CleanIn):
    """A pasted transcript without timestamps and caption tags (a preview: nothing is stored)."""
    result = clean_transcript(payload.text)
    return {
        "text": result.text,
        "timestamps": result.timestamps,
        "tags": result.tags,
        "words": result.words,
        "chars": len(result.text),
        "too_long": len(result.text) > learner.TRANSCRIPT_MAX_CHARS,
    }


@router.post("/sessions", status_code=201)
def start(payload: MessageIn, db: DbDep, settings: SettingsDep):
    """A new session from a strategy description or a transcript; stored only when it was answered."""

    def run():
        session = learner.start(db, settings, payload.kind, payload.text, payload.engine, _client, local_now(settings))
        return learner.session_out(db, settings, session)

    result = _answer(run)
    if isinstance(result, JSONResponse):
        db.rollback()
    return result


@router.get("/sessions/{session_id}")
def read(session_id: int, db: DbDep, settings: SettingsDep):
    return learner.session_out(db, settings, get_or_404(db, LearnerSession, session_id))


@router.post("/sessions/{session_id}/messages")
def send(session_id: int, payload: MessageIn, db: DbDep, settings: SettingsDep):
    """Refine the draft (chat) or add another source; the session is unchanged when the answer fails."""
    session = get_or_404(db, LearnerSession, session_id)

    def run():
        learner.reply(db, settings, session, payload.kind, payload.text, payload.engine, _client, local_now(settings))
        return learner.session_out(db, settings, session)

    result = _answer(run)
    if isinstance(result, JSONResponse):
        db.rollback()
    return result


@router.put("/sessions/{session_id}/draft")
def save_draft(session_id: int, payload: StrategyDraft, db: DbDep, settings: SettingsDep):
    """The trader's own edits to the draft (missing items and plan warnings are recomputed)."""
    session = get_or_404(db, LearnerSession, session_id)
    learner.save_draft(db, settings, session, payload)
    return learner.session_out(db, settings, session)


@router.post("/sessions/{session_id}/strategy")
def to_strategy(session_id: int, payload: StrategyIn, db: DbDep, settings: SettingsDep):
    """Create the incubator strategy (or update it while it is still in the incubator)."""
    session = get_or_404(db, LearnerSession, session_id)
    try:
        result = learner.to_strategy(
            db, settings, session, add_confluences=payload.add_confluences, today=local_today(settings).isoformat()
        )
    except learner.LearnerError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {**result, "session": learner.session_out(db, settings, session)}


@router.delete("/sessions/{session_id}", status_code=204)
def delete(session_id: int, db: DbDep):
    """Forget a session (a strategy it created stays in the incubator)."""
    db.delete(get_or_404(db, LearnerSession, session_id))
    db.commit()
    return Response(status_code=204)
