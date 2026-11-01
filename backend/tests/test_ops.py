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


def _metric(text: str, name: str, **labels) -> float:
    """Value of one sample from Prometheus text output (0 if absent)."""
    for line in text.splitlines():
        if line.startswith(f"{name}{{") and all(f'{k}="{v}"' in line for k, v in labels.items()):
            return float(line.rsplit(" ", 1)[1])
        if not labels and line.startswith(f"{name} "):
            return float(line.rsplit(" ", 1)[1])
    return 0.0


def test_metrics_endpoint_counts_requests_by_route_template_and_ingest_outcomes(client, api_key):
    before = client.get("/metrics").text
    client.get("/api/breakdown?dimension=model&days=7")
    client.get("/api/breakdown?dimension=project&days=30")
    ev = {"provider": "openai", "model": "gpt-4o", "input_tokens": 10, "request_id": "m-1"}
    client.post("/v1/events", json={"events": [ev, ev]}, headers={"X-API-Key": api_key})
    client.get("/no/such/path")
    after = client.get("/metrics")

    assert after.status_code == 200 and after.headers["content-type"].startswith("text/plain")
    text = after.text

    def delta(name, **labels):
        return _metric(text, name, **labels) - _metric(before, name, **labels)

    # Query strings never become labels: both breakdown calls land on one series.
    assert delta("burnrate_http_requests_total", method="GET", route="/api/breakdown", status="200") == 2
    assert delta("burnrate_http_requests_total", method="GET", route="unmatched", status="404") == 1
    assert delta("burnrate_events_ingested_total", outcome="accepted") == 1
    assert delta("burnrate_events_ingested_total", outcome="duplicate") == 1
    assert "burnrate_http_request_duration_seconds_bucket" in text
    assert 'route="/metrics"' not in text  # scrapes don't count themselves


def test_metrics_requires_dashboard_token_when_configured(client, monkeypatch):
    from burnrate.config import get_settings

    monkeypatch.setattr(get_settings(), "dashboard_token", "s3cret")
    assert client.get("/metrics").status_code == 401
    assert client.get("/metrics", headers={"Authorization": "Bearer s3cret"}).status_code == 200


@pytest.fixture
def fake_redis():
    import fakeredis

    return fakeredis.FakeAsyncRedis()


def test_redis_buckets_are_shared_across_replicas(fake_redis):
    import asyncio

    from burnrate.ratelimit import RedisTokenBuckets

    async def run():
        replica_a = RedisTokenBuckets(fake_redis, per_minute=60, burst=3)
        replica_b = RedisTokenBuckets(fake_redis, per_minute=60, burst=3)
        results = [await (replica_a if i % 2 else replica_b).acquire("key:x") for i in range(4)]
        other = await replica_a.acquire("key:y")
        return results, other

    results, other = asyncio.run(run())
    # Burst of 3 shared by both replicas: the 4th call is refused no matter which replica serves it.
    assert [r[0] for r in results] == [True, True, True, False]
    assert results[-1][2] > 0  # tells the caller how long to wait
    assert other[0] is True


def test_redis_store_fails_open_when_unreachable(caplog):
    import asyncio

    import redis.asyncio as redis

    from burnrate.ratelimit import RedisTokenBuckets

    dead = redis.from_url("redis://127.0.0.1:1/0", socket_connect_timeout=0.1, socket_timeout=0.1)
    buckets = RedisTokenBuckets(dead, per_minute=60, burst=1)
    with caplog.at_level(logging.WARNING):
        async def run():
            return [await buckets.acquire("k") for _ in range(3)]

        results = asyncio.run(run())
    assert all(r[0] for r in results)
    assert "rate limiter store unavailable" in caplog.text


def test_middleware_uses_shared_store(fake_redis):
    app = FastAPI()
    app.add_middleware(RateLimitMiddleware, ingest_per_minute=2, api_per_minute=0, redis_client=fake_redis)

    @app.post("/v1/events")
    def ingest():
        return {"ok": True}

    with TestClient(app) as c:
        h = {"X-API-Key": "k"}
        codes = [c.post("/v1/events", headers=h).status_code for _ in range(3)]
    assert codes == [200, 200, 429]
