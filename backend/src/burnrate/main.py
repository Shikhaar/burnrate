from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Response
from fastapi.middleware.cors import CORSMiddleware
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from sqlalchemy import text

from burnrate import __version__
from burnrate.api import admin, dashboard, ingest
from burnrate.api import auth as auth_api
from burnrate.api import settings as settings_api
from burnrate.config import get_settings
from burnrate.db import SessionLocal, engine, init_db
from burnrate.observability import REQUEST_ID_HEADER, RequestContextMiddleware, configure_logging
from burnrate.pricing import sync_registry
from burnrate.ratelimit import RateLimitMiddleware
from burnrate.security import require_dashboard_token
from burnrate.services import app_settings, secrets
from burnrate.services.scheduler import alert_timer


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    secrets.validate()  # a malformed BURNRATE_SECRET_KEY should stop startup, not the first alert
    with SessionLocal() as session:
        sync_registry(session)
        app_settings.encrypt_stored_secrets(session)
    async with alert_timer(get_settings().alert_interval_minutes):
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
    redis_client = None
    if settings.redis_url:
        import redis.asyncio as redis

        redis_client = redis.from_url(settings.redis_url, socket_timeout=0.25, socket_connect_timeout=0.25)
    app.add_middleware(
        RateLimitMiddleware,
        ingest_per_minute=settings.ingest_rate_limit_per_minute,
        api_per_minute=settings.api_rate_limit_per_minute,
        redis_client=redis_client,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,  # the dashboard's session cookie
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=[REQUEST_ID_HEADER, "Retry-After", "X-RateLimit-Limit", "X-RateLimit-Remaining"],
    )
    app.add_middleware(RequestContextMiddleware)
    app.include_router(ingest.router)
    app.include_router(auth_api.router)
    app.include_router(dashboard.router)
    app.include_router(settings_api.router)
    app.include_router(admin.router)

    @app.get("/metrics", tags=["ops"], include_in_schema=False, dependencies=[Depends(require_dashboard_token)])
    def metrics() -> Response:
        return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)

    @app.get("/healthz", tags=["ops"])
    def healthz() -> dict:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return {"status": "ok", "version": __version__}

    return app


app = create_app()
