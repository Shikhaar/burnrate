from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text

from burnrate import __version__
from burnrate.api import dashboard, ingest
from burnrate.config import get_settings
from burnrate.db import SessionLocal, engine, init_db
from burnrate.observability import REQUEST_ID_HEADER, RequestContextMiddleware, configure_logging
from burnrate.pricing import sync_registry
from burnrate.ratelimit import RateLimitMiddleware


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    with SessionLocal() as session:
        sync_registry(session)
    yield


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging(settings.log_format, settings.log_level)
    app = FastAPI(
        title="Burnrate",
        version=__version__,
        description="AI spend intelligence: where your LLM tokens and dollars go.",
        lifespan=lifespan,
    )
    # Starlette runs the last-added middleware first: request context wraps everything, so even
    # rate-limited (429) and CORS-rejected requests are logged with their request id.
    app.add_middleware(
        RateLimitMiddleware,
        ingest_per_minute=settings.ingest_rate_limit_per_minute,
        api_per_minute=settings.api_rate_limit_per_minute,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=[REQUEST_ID_HEADER, "Retry-After", "X-RateLimit-Limit", "X-RateLimit-Remaining"],
    )
    app.add_middleware(RequestContextMiddleware)
    app.include_router(ingest.router)
    app.include_router(dashboard.router)

    @app.get("/healthz", tags=["ops"])
    def healthz() -> dict:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return {"status": "ok", "version": __version__}

    return app


app = create_app()
