"""FastAPI application factory."""

from __future__ import annotations

import ipaddress
import math
from contextlib import asynccontextmanager
from urllib.parse import urlsplit

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import APP_NAME, __version__
from .api import routers
from .config import STATIC_DIR, AppConfig, load_config
from .database import create_db_engine, init_db
from .seed import seed_defaults
from .services import alerts, notify
from .services.backup import BackupError
from .services.scheduler import Scheduler
from .services.sizing import SizingError
from .services.trades import TradeBlocked, TradeError
from .services.uploads import UploadError


def create_app(config: AppConfig | None = None) -> FastAPI:
    config = config or load_config()
    engine = create_db_engine(config.db_path)
    session_factory = init_db(engine)
    with session_factory() as db:
        seed_defaults(db)
    scheduler = Scheduler(session_factory)
    notify.register_jobs(scheduler, config)
    alerts.register_jobs(scheduler, config)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        if config.background_jobs:
            scheduler.start()
        try:
            yield
        finally:
            scheduler.stop()
            engine.dispose()

    app = FastAPI(
        title=APP_NAME,
        version=__version__,
        description="Local Trading OS & Journal API",
        docs_url="/api/docs",
        redoc_url=None,
        openapi_url="/api/openapi.json",
        lifespan=lifespan,
    )
    app.state.config = config
    app.state.engine = engine
    app.state.session_factory = session_factory
    app.state.scheduler = scheduler

    for router in routers:
        app.include_router(router)
    _register_error_handlers(app)

    allowed_hosts = {"localhost", *config.allowed_hosts}

    @app.middleware("http")
    async def _same_origin_only(request: Request, call_next):
        # The API has no login: only answer to local names / IP addresses (no DNS rebinding) and refuse
        # state-changing requests sent by other websites (a form or fetch from another origin).
        host = request.headers.get("host", "")
        if not _host_allowed(host, allowed_hosts):
            return JSONResponse(
                status_code=400,
                content={"detail": "Unknown host name. Add it to TRADING_OS_ALLOWED_HOSTS to allow it."},
            )
        if request.method not in ("GET", "HEAD", "OPTIONS") and _cross_origin(request, host, config.allowed_hosts):
            return JSONResponse(status_code=403, content={"detail": "Cross-origin requests are not allowed"})
        return await call_next(request)

    @app.middleware("http")
    async def _headers(request: Request, call_next):
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        if not request.url.path.startswith(("/api/", "/uploads/")):
            # Always revalidate the UI so an upgraded app never runs stale JS.
            response.headers.setdefault("Cache-Control", "no-cache")
        return response

    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
    app.mount("/uploads", StaticFiles(directory=config.uploads_dir), name="uploads")

    @app.get("/", include_in_schema=False)
    def index() -> FileResponse:
        return FileResponse(STATIC_DIR / "index.html")

    # The Web Push service worker must be served from the root to control the whole app.
    @app.get("/sw.js", include_in_schema=False)
    def service_worker() -> FileResponse:
        return FileResponse(STATIC_DIR / "sw.js", media_type="text/javascript", headers={"Service-Worker-Allowed": "/"})

    return app


def _hostname(host: str) -> str:
    """The name part of a Host header (``[::1]:8765`` -> ``::1``, ``Localhost:80`` -> ``localhost``)."""
    host = host.strip().lower()
    if host.startswith("["):
        return host[1 : host.find("]")] if "]" in host else host
    return host.rsplit(":", 1)[0] if host.count(":") == 1 else host


def _host_allowed(host: str, allowed: set[str]) -> bool:
    name = _hostname(host)
    if name in allowed:
        return True
    try:
        ipaddress.ip_address(name)  # LAN access by IP; rebinding attacks need a DNS name
    except ValueError:
        return False
    return True


def _cross_origin(request: Request, host: str, trusted: tuple[str, ...] = ()) -> bool:
    """A write sent by another website. ``trusted`` host names (a reverse proxy or tunnel) count as this app."""
    origin = request.headers.get("origin")
    if origin is not None and urlsplit(origin).hostname in trusted:
        return False
    if request.headers.get("sec-fetch-site") == "cross-site":
        return True
    if origin is None:  # scripts, curl, platform webhooks
        return False
    return urlsplit(origin).netloc.lower() != host.strip().lower()


def _json_safe(value):
    """Validation errors echo the rejected input; Infinity/NaN must be text to be valid JSON."""
    if isinstance(value, float) and not math.isfinite(value):
        return str(value)
    if isinstance(value, dict):
        return {key: _json_safe(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    return value


def _register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(RequestValidationError)
    async def _invalid(_request: Request, exc: RequestValidationError):
        return JSONResponse(status_code=422, content={"detail": _json_safe(jsonable_encoder(exc.errors()))})

    @app.exception_handler(TradeBlocked)
    async def _blocked(_request: Request, exc: TradeBlocked):
        return JSONResponse(
            status_code=423,
            content={"detail": str(exc), "violations": [v.to_dict() for v in exc.violations]},
        )

    def _bad_request(status_code: int):
        async def handler(_request: Request, exc: Exception):
            return JSONResponse(status_code=status_code, content={"detail": str(exc)})

        return handler

    app.add_exception_handler(TradeError, _bad_request(422))
    app.add_exception_handler(SizingError, _bad_request(422))
    app.add_exception_handler(UploadError, _bad_request(400))
    app.add_exception_handler(BackupError, _bad_request(400))
