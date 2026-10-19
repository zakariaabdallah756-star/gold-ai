"""FastAPI application factory."""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import APP_NAME, __version__
from .api import routers
from .config import STATIC_DIR, AppConfig, load_config
from .database import create_db_engine, init_db
from .seed import seed_defaults
from .services.backup import BackupError
from .services.sizing import SizingError
from .services.trades import TradeBlocked, TradeError
from .services.uploads import UploadError


def create_app(config: AppConfig | None = None) -> FastAPI:
    config = config or load_config()
    engine = create_db_engine(config.db_path)
    session_factory = init_db(engine)
    with session_factory() as db:
        seed_defaults(db)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        yield
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

    for router in routers:
        app.include_router(router)
    _register_error_handlers(app)

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

    return app


def _register_error_handlers(app: FastAPI) -> None:
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
