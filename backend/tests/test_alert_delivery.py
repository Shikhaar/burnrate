"""Alert delivery: retries, re-delivery on later runs, and concurrency safety."""

import httpx
import pytest

from burnrate.config import get_settings
from burnrate.models import Alert, Budget
from burnrate.services import alerts


@pytest.fixture
def webhook(monkeypatch):
    monkeypatch.setattr(get_settings(), "alert_webhook_url", "https://hooks.example/x")


@pytest.fixture
def exceeded_budget(session, client, api_key):
    """One event and a $1 budget, so evaluation always yields exactly one budget alert."""
    client.post("/v1/events", json={"events": [{"provider": "anthropic", "model": "claude-sonnet-4-5",
                                                "input_tokens": 1_000_000}]}, headers={"X-API-Key": api_key})
    session.add(Budget(name="Tiny", monthly_limit_usd=1.0, alert_threshold=0.8))
    session.commit()


def scripted(*responses):
    """An httpx client that returns (or raises) the given responses in order."""
    calls = []
    queue = list(responses)

    def handler(request):
        calls.append(request)
        nxt = queue.pop(0)
        if isinstance(nxt, Exception):
            raise nxt
        return httpx.Response(nxt)

    return httpx.Client(transport=httpx.MockTransport(handler)), calls


def test_transient_failures_are_retried_with_backoff(session, webhook, exceeded_budget):
    client, calls = scripted(httpx.ConnectError("down"), 503, 200)
    sleeps = []
    fired = alerts.evaluate(session, client, sleep=sleeps.append)
    assert len(fired) == 1
    a = fired[0]
    assert a.delivered_at is not None and a.delivery_attempts == 3 and a.delivery_error is None
    assert len(calls) == 3 and sleeps == [0.5, 1.0]  # exponential backoff between attempts


def test_client_errors_are_not_retried(session, webhook, exceeded_budget):
    client, calls = scripted(404)
    a = alerts.evaluate(session, client, sleep=lambda s: None)[0]
    assert a.delivered_at is None and a.delivery_attempts == 1 and "HTTP 404" in a.delivery_error
    assert len(calls) == 1


def test_failed_alerts_are_redelivered_on_later_runs_until_capped(session, webhook, exceeded_budget):
    down, _ = scripted(500, 500, 500)
    fired = alerts.evaluate(session, down, sleep=lambda s: None)
    assert fired[0].delivered_at is None and fired[0].delivery_attempts == 3

    # Next run: nothing new fires, but the pending alert is retried and goes through.
    up, calls = scripted(200)
    assert alerts.evaluate(session, up, sleep=lambda s: None) == []
    stored = session.query(Alert).one()
    assert stored.delivered_at is not None and stored.delivery_attempts == 4 and len(calls) == 1

    # Delivered alerts are never sent again.
    again, calls = scripted()
    alerts.evaluate(session, again, sleep=lambda s: None)
    assert calls == []


def test_attempts_are_capped_across_runs(session, webhook, exceeded_budget):
    for _ in range(3):
        failing, _ = scripted(*([500] * alerts.ATTEMPTS_PER_RUN))
        alerts.evaluate(session, failing, sleep=lambda s: None)
    stored = session.query(Alert).one()
    assert stored.delivered_at is None and stored.delivery_attempts == alerts.MAX_DELIVERY_ATTEMPTS


def test_evaluating_twice_concurrently_cannot_double_fire(session, exceeded_budget, monkeypatch):
    """Simulate a second evaluator whose candidate list was computed before the first one committed."""
    first = alerts.evaluate(session)
    assert len(first) == 1
    real_candidates = alerts._candidates
    monkeypatch.setattr(alerts, "_candidates", lambda s: real_candidates(s))  # same alert again
    second = alerts.evaluate(session)
    assert second == [] and session.query(Alert).count() == 1


def test_budget_projection_matches_forecast(client, session):
    from burnrate.seed import generate

    generate(session, days=45, scale=1.0)
    budgets = {b["name"]: b for b in client.get("/api/budgets").json()["items"]}
    forecast = client.get("/api/forecast").json()
    assert budgets["Company AI spend"]["projected_usd"] == pytest.approx(forecast["projected_usd"], abs=0.01)
