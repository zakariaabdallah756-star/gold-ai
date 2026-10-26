"""External signal ingestion (v1.8): ``POST /api/v1/signals/incoming`` and the token management it needs.

The incoming endpoint is the only one in Trading OS authenticated by a Bearer token (a bot or a bridge posts to
it) instead of the local session; every other endpoint here manages that token for the account owner. A valid
request carries a raw signal message or already-structured fields; the signal is captured (``source="api"``) and,
when signal auto-execution is armed on the Telegram listener, sent through the same guarded path as a captured
Telegram signal. The token is write-only: only its sha256 hash and a short hint are kept, and it is never exported.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import StringConstraints, field_validator

from ..schemas import FiniteModel
from ..services import ingest
from ..services.i18n import tr
from .deps import DbDep, SettingsDep

router = APIRouter(tags=["ingest"])

Side = Annotated[str, StringConstraints(max_length=16, strip_whitespace=True)]
SourceChannel = Annotated[str, StringConstraints(max_length=ingest.SOURCE_CHANNEL_MAX, strip_whitespace=True)]


def _box(request: Request):
    return request.app.state.secrets


# ---------------------------------------------------------------- incoming signal


class IncomingSignal(FiniteModel):
    """A posted signal: a raw ``text`` message, or structured fields (symbol + action/side, with entry/sl/tp)."""

    text: Annotated[str, StringConstraints(max_length=ingest.RAW_TEXT_MAX)] = ""
    symbol: Annotated[str, StringConstraints(max_length=20, strip_whitespace=True)] = ""
    action: Side = ""  # buy | sell | long | short
    side: Side = ""  # accepted as an alias of action
    entry: float | None = None
    sl: float | None = None
    stop_loss: float | None = None  # alias of sl
    tp: list[float] | float | None = None
    take_profits: list[float] | None = None  # alias of tp
    order_type: str = "market"
    source_channel: SourceChannel = ""

    @field_validator("tp")
    @classmethod
    def _as_list(cls, value):
        if value is None:
            return None
        return [value] if isinstance(value, (int, float)) else value

    def to_input(self) -> ingest.IngestInput:
        tps = self.take_profits if self.take_profits is not None else (self.tp or [])
        return ingest.IngestInput(
            text=self.text or "",
            symbol=self.symbol or "",
            side=self.action or self.side or "",
            entry=self.entry,
            stop_loss=self.stop_loss if self.stop_loss is not None else self.sl,
            take_profits=tuple(tps),
            order_type=(self.order_type or "market").strip().lower(),
            source_channel=self.source_channel or "",
        )


def require_token(
    settings: SettingsDep,
    authorization: Annotated[str, Header()] = "",
) -> None:
    """Authenticate the Bearer token and refuse unless ingestion is enabled. Shared by the incoming endpoint."""
    scheme, _, presented = (authorization or "").partition(" ")
    presented = presented.strip() if scheme.lower() == "bearer" else ""
    if not ingest.token_matches(settings, presented):
        raise HTTPException(status_code=401, detail=tr("Invalid or missing API token"))
    if not settings.ingest_enabled:
        raise HTTPException(status_code=403, detail=tr("Signal ingestion is turned off"))


@router.post("/api/v1/signals/incoming")
def incoming(
    payload: IncomingSignal,
    request: Request,
    db: DbDep,
    settings: SettingsDep,
    _: Annotated[None, Depends(require_token)],
):
    """Accept an external signal, capture it, and auto-execute it when the listener is armed."""
    try:
        return ingest.ingest(db, settings, _box(request), payload.to_input())
    except ingest.IngestError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


# -------------------------------------------------------------- token management


class TokenIn(FiniteModel):
    token: Annotated[str, StringConstraints(max_length=200)]


class EnableIn(FiniteModel):
    enabled: bool


@router.get("/api/ingest/status")
def status(settings: SettingsDep):
    return ingest.status(settings)


@router.put("/api/ingest/enabled")
def set_enabled(payload: EnableIn, db: DbDep, settings: SettingsDep):
    """Turn ingestion on or off. Turning it on needs a token to be set first."""
    if payload.enabled and not settings.ingest_token_hash:
        raise HTTPException(status_code=409, detail=tr("Set an API token before turning ingestion on"))
    settings.ingest_enabled = payload.enabled
    db.commit()
    return ingest.status(settings)


@router.post("/api/ingest/token")
def set_token(payload: TokenIn, db: DbDep, settings: SettingsDep):
    """Store (or with an empty token remove) the ingestion token. Only its sha256 hash is kept."""
    try:
        ingest.set_token(settings, payload.token)
    except ingest.IngestError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    db.commit()
    return ingest.status(settings)


@router.post("/api/ingest/token/generate")
def generate_token(db: DbDep, settings: SettingsDep):
    """Generate a fresh token, store its hash, and return the token once (the caller must copy it now)."""
    token = ingest.new_token()
    ingest.set_token(settings, token)
    settings.ingest_enabled = True
    db.commit()
    return {**ingest.status(settings), "token": token}
