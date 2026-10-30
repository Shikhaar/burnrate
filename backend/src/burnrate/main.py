from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text

from burnrate import __version__
from burnrate.api import dashboard, ingest
from burnrate.config import get_settings
from burnrate.db import SessionLocal, engine, init_db
from burnrate.pricing import sync_registry


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    with SessionLocal() as session:
        sync_registry(session)
    yield


def create_app() -> FastAPI:
    app = FastAPI(
        title="Burnrate",
        version=__version__,
        description="AI spend intelligence: where your LLM tokens and dollars go.",
        lifespan=lifespan,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=get_settings().cors_origins,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(ingest.router)
    app.include_router(dashboard.router)

    @app.get("/healthz", tags=["ops"])
    def healthz() -> dict:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return {"status": "ok", "version": __version__}

    return app


app = create_app()
