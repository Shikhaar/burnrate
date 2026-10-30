import os

import pytest

os.environ["BURNRATE_DATABASE_URL"] = "sqlite:///:memory:"

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402

from burnrate import db  # noqa: E402
from burnrate.main import create_app  # noqa: E402
from burnrate.pricing import sync_registry  # noqa: E402
from burnrate.security import create_api_key  # noqa: E402


@pytest.fixture
def engine():
    from sqlalchemy import create_engine

    eng = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    db.init_db(eng)
    return eng


@pytest.fixture
def session(engine):
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    with Session() as s:
        sync_registry(s)
        yield s


@pytest.fixture
def client(engine, session, monkeypatch):
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    monkeypatch.setattr(db, "engine", engine)
    monkeypatch.setattr(db, "SessionLocal", Session)
    import burnrate.main as main

    monkeypatch.setattr(main, "engine", engine)
    monkeypatch.setattr(main, "SessionLocal", Session)
    app = create_app()

    def _session():
        with Session() as s:
            yield s

    app.dependency_overrides[db.get_session] = _session
    with TestClient(app) as c:
        yield c


@pytest.fixture
def api_key(session) -> str:
    return create_api_key(session, "test")
