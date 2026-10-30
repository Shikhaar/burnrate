from datetime import UTC, datetime, timedelta

import pytest

from burnrate.seed import generate


def event(**kw):
    return {"provider": "anthropic", "model": "claude-sonnet-4-5", "project": "support", "feature": "triage",
            "input_tokens": 1_000_000, "output_tokens": 100_000, **kw}


def test_ingest_requires_valid_key(client):
    assert client.post("/v1/events", json={"events": [event()]}).status_code == 401
    assert client.post("/v1/events", json={"events": [event()]}, headers={"X-API-Key": "nope"}).status_code == 401


def test_ingest_prices_events_and_is_idempotent(client, api_key):
    h = {"X-API-Key": api_key}
    r = client.post("/v1/events", json={"events": [event(request_id="r1"), event(request_id="r1")]}, headers=h)
    assert r.status_code == 202
    assert r.json()["accepted"] == 1 and r.json()["duplicates"] == 1
    assert client.post("/v1/events", json={"events": [event(request_id="r1")]}, headers=h).json()["duplicates"] == 1

    ov = client.get("/api/overview?days=7").json()
    # 1M input @ $3 + 100k output @ $15 = $4.50
    assert ov["total_cost_usd"] == pytest.approx(4.5)
    assert ov["request_count"] == 1
    assert ov["by_provider"][0]["key"] == "anthropic"


def test_ingest_validates_payload(client, api_key):
    r = client.post("/v1/events", json={"events": [event(input_tokens=-5)]}, headers={"X-API-Key": api_key})
    assert r.status_code == 422


def test_otlp_genai_spans_become_events(client, api_key):
    now_ns = int(datetime.now(UTC).timestamp() * 1e9)

    def attr(k, v):
        return {"key": k, "value": {"intValue": v} if isinstance(v, int) else {"stringValue": v}}

    payload = {"resourceSpans": [{
        "resource": {"attributes": [attr("service.name", "chatbot")]},
        "scopeSpans": [{"spans": [
            {"traceId": "t1", "spanId": "s1", "startTimeUnixNano": now_ns, "endTimeUnixNano": now_ns + 2_000_000_000,
             "attributes": [attr("gen_ai.system", "openai"), attr("gen_ai.response.model", "gpt-4o-2024-08-06"),
                            attr("gen_ai.usage.input_tokens", 1000), attr("gen_ai.usage.output_tokens", 200),
                            attr("burnrate.feature", "faq")]},
            {"traceId": "t1", "spanId": "s2", "attributes": [attr("http.method", "GET")]},
        ]}],
    }]}
    r = client.post("/v1/otlp/v1/traces", json=payload, headers={"X-API-Key": api_key}).json()
    assert r["accepted"] == 1
    item = client.get("/api/events").json()["items"][0]
    assert item["project"] == "chatbot" and item["feature"] == "faq" and item["latency_ms"] == 2000
    assert item["cost_usd"] == pytest.approx(1000 * 2.5 / 1e6 + 200 * 10 / 1e6)


def test_budget_crud_and_status(client, api_key):
    client.post("/v1/events", json={"events": [event()]}, headers={"X-API-Key": api_key})
    r = client.post("/api/budgets", json={"name": "Support", "scope_dimension": "project", "scope_value": "support",
                                          "monthly_limit_usd": 4.0})
    assert r.status_code == 201
    b = r.json()
    assert b["spent_usd"] == pytest.approx(4.5) and b["status"] == "exceeded"
    bad = client.post("/api/budgets", json={"name": "x", "scope_dimension": "project", "monthly_limit_usd": 1})
    assert bad.status_code == 422
    assert client.delete(f"/api/budgets/{b['id']}").status_code == 204
    assert client.get("/api/budgets").json()["items"] == []


@pytest.fixture
def seeded(client, session):
    generate(session, days=45, scale=2.0)
    return client


def test_demo_dataset_surfaces_planted_signals(seeded):
    anomalies = seeded.get("/api/anomalies?days=7").json()["items"]
    assert any(a["key"] == "ticket-triage" and a["severity"] == "high" for a in anomalies)

    insights = seeded.get("/api/insights").json()["items"]
    kinds = {i["kind"] for i in insights}
    assert {"prompt_caching", "model_downgrade"} <= kinds
    savings = [i["estimated_monthly_savings_usd"] for i in insights]
    assert savings == sorted(savings, reverse=True)

    recon = {i["provider"]: i for i in seeded.get("/api/reconciliation").json()["items"]}
    assert recon["google"]["status"] == "investigate"
    assert recon["anthropic"]["status"] == "ok"


def test_breakdown_shares_sum_to_one(seeded):
    items = seeded.get("/api/breakdown?dimension=project&limit=3").json()["items"]
    assert items[-1]["key"] == "(other)"
    assert sum(i["share"] for i in items) == pytest.approx(1.0, abs=1e-3)


def test_timeseries_and_forecast_shapes(seeded):
    ts = seeded.get("/api/timeseries?group_by=provider&days=14").json()
    assert len(ts["points"]) == 14
    assert set(ts["points"][0]["values"]) == set(ts["series_keys"])

    fc = seeded.get("/api/forecast").json()
    assert fc["low_usd"] <= fc["projected_usd"] <= fc["high_usd"]
    assert fc["projected_usd"] >= fc["month_to_date_usd"]
    today = datetime.now(UTC).date()
    assert fc["daily"][today.day - 1]["actual_usd"] is not None
    if (today + timedelta(days=1)).month == today.month:
        assert fc["daily"][today.day]["forecast_usd"] is not None


def test_dashboard_token_enforced_when_configured(client, monkeypatch):
    from burnrate.config import get_settings

    monkeypatch.setattr(get_settings(), "dashboard_token", "s3cret")
    assert client.get("/api/meta").status_code == 401
    assert client.get("/api/meta", headers={"Authorization": "Bearer s3cret"}).status_code == 200


def test_alerts_fire_once_and_deliver_to_webhook(seeded, session, monkeypatch):
    import httpx

    from burnrate.config import get_settings
    from burnrate.services.alerts import evaluate

    posted = []
    monkeypatch.setattr(get_settings(), "alert_webhook_url", "https://hooks.example/x")
    client = httpx.Client(transport=httpx.MockTransport(lambda r: posted.append(r) or httpx.Response(200)))

    seeded.post("/api/budgets", json={"name": "Tight", "monthly_limit_usd": 1.0})
    fired = evaluate(session, client)
    assert any(a.kind == "budget" and a.title == "Budget exceeded: Tight" for a in fired)
    assert any(a.kind == "anomaly" and "ticket-triage" in a.title for a in fired)
    assert len(posted) == len(fired) and all(a.delivered_at for a in fired)

    again = evaluate(session, httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200))))
    assert again == []
    assert len(seeded.get("/api/alerts").json()["items"]) == len(fired)
