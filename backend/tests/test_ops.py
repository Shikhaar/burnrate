"""Rate limiting and structured logging."""

import json
import logging

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from burnrate.observability import JsonFormatter, RequestContextMiddleware, RequestIdFilter, request_id_var
from burnrate.ratelimit import RateLimitMiddleware, TokenBuckets


def test_token_bucket_allows_burst_then_refills():
    b = TokenBuckets(per_minute=60, burst=3)  # 1 token/second
    assert [b.take("k", now=0)[0] for _ in range(4)] == [True, True, True, False]
    allowed, _, wait = b.take("k", now=0)
    assert not allowed and wait == pytest.approx(1.0)
    assert b.take("k", now=1.01)[0] is True  # one token refilled
    assert b.take("other", now=0)[0] is True  # keys are independent


def test_token_bucket_evicts_idle_keys():
    b = TokenBuckets(per_minute=60, max_keys=2)
    for k in ("a", "b", "c"):
        b.take(k, now=0)
    assert list(b._buckets) == ["b", "c"]


@pytest.fixture
def limited_app():
    app = FastAPI()
    app.add_middleware(RateLimitMiddleware, ingest_per_minute=2, api_per_minute=3)

    @app.post("/v1/events")
    def ingest():
        return {"ok": True}

    @app.get("/api/meta")
    def meta():
        return {"ok": True}

    @app.get("/healthz")
    def health():
        return {"ok": True}

    return TestClient(app)


def test_ingest_limited_per_api_key(limited_app):
    a, b = {"X-API-Key": "key-a"}, {"X-API-Key": "key-b"}
    assert [limited_app.post("/v1/events", headers=a).status_code for _ in range(3)] == [200, 200, 429]
    r = limited_app.post("/v1/events", headers=a)
    assert r.status_code == 429 and int(r.headers["Retry-After"]) >= 1
    assert r.headers["X-RateLimit-Remaining"] == "0"
    assert limited_app.post("/v1/events", headers=b).status_code == 200  # other keys unaffected


def test_dashboard_limited_per_ip_and_health_exempt(limited_app):
    codes = [limited_app.get("/api/meta").status_code for _ in range(4)]
    assert codes == [200, 200, 200, 429]
    ok = limited_app.get("/api/meta")
    assert ok.status_code == 429
    assert all(limited_app.get("/healthz").status_code == 200 for _ in range(10))


def test_real_app_returns_429_with_cors_headers(client, monkeypatch):
    """Through the full middleware stack: a 429 still carries CORS + request id so browsers can read it."""
    from burnrate.config import get_settings
    from burnrate.main import create_app

    monkeypatch.setattr(get_settings(), "api_rate_limit_per_minute", 2)
    app = create_app()
    app.dependency_overrides = client.app.dependency_overrides
    with TestClient(app) as c:
        h = {"Origin": "http://localhost:3000"}
        codes = [c.get("/api/pricing", headers=h).status_code for _ in range(3)]
        r = c.get("/api/pricing", headers=h)
    assert codes == [200, 200, 429] and r.status_code == 429
    assert r.headers["access-control-allow-origin"] == "http://localhost:3000"
    assert r.headers["x-request-id"]


def test_request_id_is_propagated_and_logged_as_json(caplog):
    app = FastAPI()
    app.add_middleware(RequestContextMiddleware)

    @app.get("/thing")
    def thing():
        logging.getLogger("burnrate.test").info("inside handler", extra={"answer": 42})
        return {"rid": request_id_var.get()}

    caplog.handler.addFilter(RequestIdFilter())
    with caplog.at_level(logging.INFO), TestClient(app) as c:
        r = c.get("/thing", headers={"X-Request-ID": "abc123"})
        generated = c.get("/thing")

    assert r.json()["rid"] == "abc123" and r.headers["X-Request-ID"] == "abc123"
    assert len(generated.headers["X-Request-ID"]) == 32  # uuid4 hex when none supplied

    fmt = JsonFormatter()
    inside = json.loads(fmt.format(next(r for r in caplog.records if r.getMessage() == "inside handler")))
    assert inside["answer"] == 42 and inside["level"] == "info" and inside["logger"] == "burnrate.test"
    assert inside["request_id"] == "abc123"

    access = [json.loads(fmt.format(rec)) for rec in caplog.records if rec.getMessage() == "request"]
    assert {"method", "path", "status", "duration_ms"} <= set(access[0])
    assert access[0]["path"] == "/thing" and access[0]["status"] == 200 and access[0]["request_id"] == "abc123"
