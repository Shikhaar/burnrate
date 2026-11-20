"""Traces: calls grouped by trace_id, with per-step cost, timing, retries and quality scores."""

from datetime import UTC, datetime, timedelta

import httpx
import pytest

from burnrate import auth
from burnrate.config import get_settings
from burnrate.models import TraceScore

PASSWORD = "correct horse battery"
CSRF = {"X-Requested-With": "burnrate"}


def _now() -> datetime:
    return datetime.now(UTC).replace(microsecond=0) - timedelta(hours=1)


def _ev(trace_id, feature, offset_s, *, model="gpt-4o", team="support", status="success", latency=1000,
        input_tokens=1000, output_tokens=100, **kw):
    return {"provider": "openai", "model": model, "project": "support-agent", "feature": feature, "team": team,
            "trace_id": trace_id, "timestamp": (_now() + timedelta(seconds=offset_s)).isoformat(),
            "input_tokens": input_tokens, "output_tokens": output_tokens, "latency_ms": latency,
            "status": status, **kw}


def _send(client, api_key, events):
    r = client.post("/v1/events", json={"events": events}, headers={"X-API-Key": api_key})
    assert r.status_code == 202, r.text
    return r.json()


@pytest.fixture
def traced(client, api_key):
    _send(client, api_key, [
        # t1: triage retried twice, then a reply; the costliest run
        _ev("t1", "triage", 0, customer_id="acme"), _ev("t1", "triage", 2, status="error"),
        _ev("t1", "triage", 4), _ev("t1", "reply", 6, model="gpt-4.1", output_tokens=2000, latency=3000),
        # t2: a clean two-step run for another customer
        _ev("t2", "triage", 10, customer_id="globex"), _ev("t2", "reply", 12, model="gpt-4.1"),
        # t3: one feature throughout, on another team's project
        _ev("t3", "embed", 20, project="search", team="search", model="text-embedding-3-small", output_tokens=0),
        _ev("t3", "embed", 21, project="search", team="search", model="text-embedding-3-small", output_tokens=0),
        # not part of any trace
        _ev(None, "triage", 30),
    ])
    return client


def test_traces_are_grouped_costed_and_named(traced):
    body = traced.get("/api/traces?days=1").json()
    assert body["summary"]["traces"] == 3 and body["summary"]["avg_calls"] == pytest.approx(8 / 3, abs=0.01)
    items = {t["trace_id"]: t for t in body["items"]}
    assert [t["trace_id"] for t in body["items"]][0] == "t1"  # sorted by cost

    t1 = items["t1"]
    # Several steps: named after the project. One step throughout: named after it.
    assert (t1["name"], items["t3"]["name"]) == ("support-agent", "embed")
    assert (t1["calls"], t1["errors"], t1["customer_id"]) == (4, 1, "acme")
    assert t1["steps"] == ["triage", "triage", "triage", "reply"] and t1["models"] == ["gpt-4o", "gpt-4.1"]
    assert t1["duration_ms"] == 9000  # last step starts at +6s and runs 3s
    assert t1["cost_usd"] == pytest.approx(3 * (1000 * 2.5 + 100 * 10) / 1e6 + (1000 * 2 + 2000 * 8) / 1e6)

    wf = {w["name"]: w for w in body["workflows"]}
    assert wf["support-agent"]["traces"] == 2 and wf["support-agent"]["avg_calls"] == 3
    assert wf["support-agent"]["failed_share"] == 0.5

    assert [t["trace_id"] for t in traced.get("/api/traces?days=1&sort=recent").json()["items"]] == ["t3", "t2", "t1"]
    assert [t["trace_id"] for t in traced.get("/api/traces?days=1&errors=true").json()["items"]] == ["t1"]
    # Filters match a trace if any step matches.
    assert [t["trace_id"] for t in traced.get("/api/traces?days=1&customer=globex").json()["items"]] == ["t2"]
    assert {t["trace_id"] for t in traced.get("/api/traces?days=1&feature=reply").json()["items"]} == {"t1", "t2"}
    page = traced.get("/api/traces?days=1&limit=1&offset=1").json()
    assert page["total"] == 3 and [t["trace_id"] for t in page["items"]] == ["t2"]


def test_trace_detail_shows_steps_shares_and_repeats(traced):
    t = traced.get("/api/traces/t1").json()
    assert (t["calls"], t["errors"], t["repeats"], t["top_step"]) == (4, 1, 2, "triage")
    assert [s["offset_ms"] for s in t["steps"]] == [0, 2000, 4000, 6000]
    assert [s["repeat"] for s in t["steps"]] == [False, True, True, False]
    assert sum(s["cost_share"] for s in t["steps"]) == pytest.approx(1, abs=0.001)
    assert t["steps"][3]["cost_share"] > 0.5  # the reply is most of the money
    assert traced.get("/api/traces/nope").status_code == 404


def test_scores_are_ingested_idempotently_and_shown_with_cost(traced, api_key):
    send = {"scores": [
        {"trace_id": "t1", "name": "resolved", "value": False, "id": "s1"},
        {"trace_id": "t1", "name": "helpfulness", "value": 0.4, "id": "s2"},
        {"trace_id": "t1", "name": "helpfulness", "value": 0.6},
        {"trace_id": "t2", "name": "resolved", "value": True, "id": "s3"},
        {"trace_id": "t2", "name": "outcome", "label": "escalated", "id": "s4"},
    ]}
    r = traced.post("/v1/scores", json=send, headers={"X-API-Key": api_key}).json()
    assert (r["accepted"], r["duplicates"]) == (5, 0)
    again = traced.post("/v1/scores", json={"scores": send["scores"][:2]}, headers={"X-API-Key": api_key}).json()
    assert (again["accepted"], again["duplicates"]) == (0, 2)
    assert traced.post("/v1/scores", json={"scores": [{"trace_id": "t1", "name": "x"}]},
                       headers={"X-API-Key": api_key}).status_code == 422  # neither value nor label
    assert traced.post("/v1/scores", json=send).status_code == 401  # needs an ingest key

    items = {t["trace_id"]: t for t in traced.get("/api/traces?days=1").json()["items"]}
    assert items["t1"]["scores"] == {"resolved": 0.0, "helpfulness": 0.5}
    assert items["t2"]["scores"] == {"resolved": 1.0, "outcome": "escalated"}
    wf = {w["name"]: w for w in traced.get("/api/traces?days=1").json()["workflows"]}
    assert wf["support-agent"]["scores"] == {"resolved": 0.5, "helpfulness": 0.5}
    detail = traced.get("/api/traces/t2").json()["scores"]
    assert {(s["name"], s["data_type"], s["value"], s["label"]) for s in detail} == {
        ("resolved", "BOOLEAN", 1.0, None), ("outcome", "CATEGORICAL", None, "escalated")}


def test_team_limited_users_only_see_their_teams_traces(traced, session, monkeypatch):
    monkeypatch.setattr(get_settings(), "cookie_secure", False)
    auth.create_user(session, "root@acme.io", PASSWORD, role="superadmin")
    auth.create_user(session, "sam@acme.io", PASSWORD, role="viewer", teams=["search"])
    session.add(TraceScore(trace_id="t1", name="resolved", value=1.0, data_type="BOOLEAN", source="api"))
    session.commit()
    assert traced.post("/api/auth/login", json={"email": "sam@acme.io", "password": PASSWORD}).status_code == 200
    body = traced.get("/api/traces?days=1").json()
    assert [t["trace_id"] for t in body["items"]] == ["t3"] and body["summary"]["traces"] == 1
    assert [w["name"] for w in body["workflows"]] == ["embed"]
    assert traced.get("/api/traces/t1").status_code == 404  # another team's run, scores included


def test_trace_links_point_to_where_the_prompts_are(traced, api_key, monkeypatch):
    assert traced.get("/api/traces/t2").json()["link"] is None
    monkeypatch.setattr(get_settings(), "trace_url_template", "https://phoenix.internal/traces/{trace_id}")
    assert traced.get("/api/traces/t2").json()["link"] == "https://phoenix.internal/traces/t2"
    _send(traced, api_key, [_ev("t9", "x", 40, attributes={"trace_url": "https://lf.example/project/p/traces/t9"})])
    assert traced.get("/api/traces/t9").json()["link"] == "https://lf.example/project/p/traces/t9"


def test_langfuse_scores_are_imported_for_traces(session, monkeypatch):
    from burnrate.connectors import sync_langfuse_scores

    monkeypatch.setattr(get_settings(), "langfuse_host", "https://cloud.langfuse.com")
    monkeypatch.setattr(get_settings(), "langfuse_public_key", "pk")
    monkeypatch.setattr(get_settings(), "langfuse_secret_key", "sk")
    pages = {
        None: ([
            {"id": "sc1", "name": "correctness", "dataType": "NUMERIC", "value": 0.9,
             "timestamp": "2026-11-19T10:00:00Z", "subject": {"kind": "trace", "id": "tr1"}},
            {"id": "sc2", "name": "thumbs", "dataType": "BOOLEAN", "value": True,
             "subject": {"kind": "observation", "id": "obs1", "traceId": "tr1"}},
            {"id": "sc3", "name": "session-csat", "dataType": "NUMERIC", "value": 4,
             "subject": {"kind": "session", "id": "sess1"}},  # no trace: skipped
        ], "next"),
        "next": ([{"id": "sc4", "name": "topic", "dataType": "CATEGORICAL", "value": "billing",
                   "subject": {"kind": "trace", "id": "tr2"}}], None),
    }

    def handler(req: httpx.Request) -> httpx.Response:
        assert req.url.path == "/api/public/v3/scores"
        p = req.url.params
        assert p["fields"] == "subject" and p["limit"] == "100" and p["dataType"] == "NUMERIC,BOOLEAN,CATEGORICAL"
        data, cursor = pages[p.get("cursor")]
        meta = {"limit": 100, **({"cursor": cursor} if cursor else {})}
        return httpx.Response(200, json={"data": data, "meta": meta})

    from datetime import date

    def run():
        return sync_langfuse_scores(session, date(2026, 11, 18), httpx.Client(transport=httpx.MockTransport(handler)))

    first = run()
    assert (first.accepted, first.duplicates) == (3, 0)
    assert run().duplicates == 3
    rows = {(s.trace_id, s.name): s for s in session.query(TraceScore)}
    assert rows[("tr1", "correctness")].value == 0.9 and rows[("tr1", "correctness")].source == "langfuse"
    assert (rows[("tr1", "thumbs")].data_type, rows[("tr1", "thumbs")].value) == ("BOOLEAN", 1.0)
    assert rows[("tr2", "topic")].label == "billing"
    assert rows[("tr1", "correctness")].external_id == "langfuse:sc1"


def test_demo_data_has_traces_with_scores(session):
    from burnrate.seed import generate
    from burnrate.services.traces import get_trace, list_traces

    generate(session, days=4, scale=0.3)
    body = list_traces(session, days=4, limit=200)
    names = {w["name"] for w in body["workflows"]}
    assert {"support-agent", "rag-search"} <= names
    assert all(t["calls"] >= 2 for t in body["items"])
    assert session.query(TraceScore).count() > 0
    # The planted retry loop: recent support runs repeat the triage step.
    support = [t for t in list_traces(session, days=2, limit=200)["items"] if t["name"] == "support-agent"]
    assert max(get_trace(session, t["trace_id"])["repeats"] for t in support) >= 2
