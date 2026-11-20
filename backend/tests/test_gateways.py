"""LiteLLM and Langfuse importers: calls logged there become Burnrate events, priced by Burnrate."""

import base64
import json
from datetime import UTC, date, datetime, timedelta

import httpx
import pytest

from burnrate.config import get_settings
from burnrate.connectors import sync_usage
from burnrate.connectors.langfuse import split_usage
from burnrate.connectors.providers import resolve
from burnrate.models import EventCost, UsageEvent


def _costs(session) -> dict[str, EventCost]:
    events = {e.id: e for e in session.query(UsageEvent)}
    return {events[c.event_id].request_id: c for c in session.query(EventCost)}


def _events(session) -> dict[str, UsageEvent]:
    return {e.request_id: e for e in session.query(UsageEvent)}


@pytest.mark.parametrize("model,hint,expected", [
    ("gpt-4o-2024-08-06", None, ("openai", "gpt-4o-2024-08-06")),
    ("anthropic/claude-sonnet-4-5", None, ("anthropic", "claude-sonnet-4-5")),
    ("claude-haiku-4-5", "anthropic", ("anthropic", "claude-haiku-4-5")),
    ("gemini-2.5-flash", "vertex_ai", ("google", "gemini-2.5-flash")),
    ("gemini/gemini-2.5-pro", None, ("google", "gemini-2.5-pro")),
    ("gpt-4o", "azure", ("azure", "gpt-4o")),  # Azure bills separately from OpenAI
    ("anthropic.claude-3-5-sonnet-v2:0", "bedrock", ("bedrock", "anthropic.claude-3-5-sonnet-v2:0")),
    ("mistral-large-latest", None, ("mistral", "mistral-large-latest")),
    ("llama-3.3-70b", None, ("unknown", "llama-3.3-70b")),
])
def test_provider_is_worked_out_from_hint_or_model_name(model, hint, expected):
    assert resolve(model, hint) == expected


# ---- LiteLLM ---------------------------------------------------------------------------------------------
def _spend_log(request_id, **kw):
    row = {
        "request_id": request_id, "call_type": "acompletion", "api_key": "hashed", "spend": 0.0123,
        "total_tokens": 1500, "prompt_tokens": 1000, "completion_tokens": 500,
        "startTime": "2026-11-19T10:00:00", "endTime": "2026-11-19T10:00:01.250000",
        "model": "gpt-4o", "model_group": "product-chat", "custom_llm_provider": "openai",
        "user": "internal-user-1", "end_user": "acme-corp", "team_id": "team-uuid",
        "metadata": {"user_api_key_alias": "support-bot", "user_api_key_team_alias": "support",
                     "additional_usage_values": {"cache_read_input_tokens": 400},
                     "usage_object": {"completion_tokens_details": {"reasoning_tokens": 0}}},
        "cache_hit": "False", "request_tags": ["beta"], "status": "success",
    }
    row.update(kw)
    return row


def test_litellm_spend_logs_become_priced_events(session, monkeypatch):
    monkeypatch.setattr(get_settings(), "litellm_url", "https://litellm.internal/")
    monkeypatch.setattr(get_settings(), "litellm_api_key", "sk-viewer")
    today = datetime.now(UTC).date()
    yesterday = today - timedelta(days=1)
    rows = {
        (yesterday.isoformat(), 1): [
            _spend_log("chatcmpl-1", session_id="sess-1"),
            _spend_log("chatcmpl-2", model="claude-sonnet-4-5", custom_llm_provider="anthropic", spend=0.02,
                       prompt_tokens=2300, completion_tokens=100,
                       metadata={"spend_logs_metadata": {"project": "search", "feature": "rerank",
                                                         "customer_id": "globex", "environment": "staging"},
                                 "additional_usage_values": {"cache_read_input_tokens": 2000,
                                                             "cache_creation_input_tokens": 200}}),
        ],
        # LiteLLM capped the count, so we keep paging until an empty page.
        (yesterday.isoformat(), 2): [
            _spend_log("chatcmpl-3", model="gpt-4o", custom_llm_provider="azure", spend=0.5),
            _spend_log("chatcmpl-4", cache_hit="True", spend=0.0),
            _spend_log("chatcmpl-5", status="failure", spend=0.0, prompt_tokens=0, completion_tokens=0),
        ],
    }
    seen = []

    def handler(req: httpx.Request) -> httpx.Response:
        assert req.url.path == "/spend/logs/v2"
        assert req.headers["authorization"] == "Bearer sk-viewer"
        p = req.url.params
        day = p["start_date"][:10]
        assert p["start_date"] == f"{day} 00:00:00" and p["end_date"] == f"{day} 23:59:59"
        assert p["page_size"] == "1000"
        seen.append((day, int(p["page"])))
        data = rows.get((day, int(p["page"])), [])
        return httpx.Response(200, json={"data": data, "total": 10000, "page": int(p["page"]), "page_size": 1000,
                                         "total_pages": 1, "total_is_capped": day == yesterday.isoformat()})

    def run():
        return sync_usage(session, "litellm", yesterday, httpx.Client(transport=httpx.MockTransport(handler)))

    first = run()
    assert (first.accepted, first.duplicates) == (5, 0)
    # Yesterday: pages 1, 2, then the empty page 3 ends it. Today: one uncapped page.
    assert seen == [(yesterday.isoformat(), 1), (yesterday.isoformat(), 2), (yesterday.isoformat(), 3),
                    (today.isoformat(), 1)]
    assert (run().accepted, run().duplicates) == (0, 5)

    ev, costs = _events(session), _costs(session)
    openai = ev["litellm:chatcmpl-1"]
    assert (openai.provider, openai.model, openai.source) == ("openai", "gpt-4o", "litellm")
    # Burnrate attribution falls back to LiteLLM's own: key alias, model group, team alias, end user.
    assert (openai.project, openai.feature, openai.team, openai.customer_id, openai.user_id) == \
        ("support-bot", "product-chat", "support", "acme-corp", "internal-user-1")
    # prompt_tokens includes the 400 cache reads.
    assert (openai.input_tokens, openai.cached_input_tokens, openai.output_tokens) == (600, 400, 500)
    assert openai.trace_id == "sess-1" and ev["litellm:chatcmpl-3"].trace_id is None
    assert openai.latency_ms == 1250 and openai.timestamp.replace(tzinfo=UTC) == datetime(2026, 11, 19, 10, tzinfo=UTC)
    assert costs[openai.request_id].method == "priced"  # Burnrate's price, not LiteLLM's 0.0123
    assert costs[openai.request_id].total_cost_usd == pytest.approx((600 * 2.5 + 400 * 1.25 + 500 * 10) / 1e6)

    claude = ev["litellm:chatcmpl-2"]
    assert (claude.project, claude.feature, claude.customer_id, claude.environment) == \
        ("search", "rerank", "globex", "staging")
    assert (claude.input_tokens, claude.cached_input_tokens, claude.cache_write_tokens) == (100, 2000, 200)
    assert costs[claude.request_id].method == "priced"

    azure = ev["litellm:chatcmpl-3"]
    assert azure.provider == "azure"
    assert (costs[azure.request_id].method, costs[azure.request_id].total_cost_usd) == ("provider_reported", 0.5)

    hit = ev["litellm:chatcmpl-4"]
    assert (hit.input_tokens, hit.output_tokens, costs[hit.request_id].total_cost_usd) == (0, 0, 0)
    assert hit.attributes["litellm_cache_hit"] is True
    assert ev["litellm:chatcmpl-5"].status == "error"


def test_litellm_requires_configuration(session, monkeypatch):
    monkeypatch.setattr(get_settings(), "litellm_url", "")
    with pytest.raises(SystemExit, match="BURNRATE_LITELLM_URL"):
        sync_usage(session, "litellm", date(2026, 11, 1))


# ---- Langfuse --------------------------------------------------------------------------------------------
def test_langfuse_usage_buckets_are_exclusive():
    assert split_usage({"input": 86, "input_cached_tokens": 17817, "output": 150, "output_reasoning_tokens": 38,
                        "input_audio_tokens": 10, "total": 18101}) == {
        "input_tokens": 96, "cached_input_tokens": 17817, "cache_write_tokens": 0,
        "output_tokens": 188, "reasoning_tokens": 38}
    assert split_usage({"input": 5, "cache_read_input_tokens": 900, "cache_creation_input_tokens": 100,
                        "output": 20})["cache_write_tokens"] == 100


def _obs(obs_id, **kw):
    o = {"id": obs_id, "traceId": "a" * 32, "projectId": "proj1", "startTime": "2026-11-19T09:00:00.000Z",
         "type": "GENERATION",
         "name": "llm-call", "level": "DEFAULT", "environment": "default", "userId": "user-7",
         "model": "gpt-4o-mini", "usageDetails": {"input": 1000, "output": 200, "total": 1200},
         "totalCost": 0.0003, "latency": 0.8, "traceName": "support-chat", "tags": ["prod"],
         "metadata": {"customer_id": "acme-corp"}}
    o.update(kw)
    return o


def test_langfuse_generations_become_priced_events(session, monkeypatch):
    monkeypatch.setattr(get_settings(), "langfuse_host", "https://langfuse.acme.dev/")
    monkeypatch.setattr(get_settings(), "langfuse_public_key", "pk-lf-1")
    monkeypatch.setattr(get_settings(), "langfuse_secret_key", "sk-lf-1")
    pages = {
        ("GENERATION", None): ([_obs("o1"), _obs("o2", model="claude-sonnet-4-5",
                                                usageDetails={"input": 50, "cache_read_input_tokens": 1000,
                                                              "output": 80},
                                                metadata={"project": "agents", "feature": "planner", "team": "ml"},
                                                environment="staging")], "c1"),
        ("GENERATION", "c1"): ([], "c2"),  # an empty page with a cursor isn't the end
        ("GENERATION", "c2"): ([_obs("o3", model="llama-3.3-70b", totalCost=0.01, level="ERROR"),
                                _obs("o4", model=None)], None),
        ("EMBEDDING", None): ([_obs("e1", type="EMBEDDING", model="text-embedding-3-small",
                                    usageDetails={"input": 5000}, traceName=None, name="embed-docs")], None),
    }
    calls = []

    def handler(req: httpx.Request) -> httpx.Response:
        assert req.headers["authorization"] == "Basic " + base64.b64encode(b"pk-lf-1:sk-lf-1").decode()
        if req.url.path == "/api/public/projects":
            return httpx.Response(200, json={"data": [{"id": "p1", "name": "Support App"}]})
        assert req.url.path == "/api/public/v2/observations"
        p = req.url.params
        assert p["limit"] == "1000" and "usage" in p["fields"] and p["fromStartTime"].startswith("2026-11-18T00:00:00")
        calls.append((p["type"], p.get("cursor")))
        data, cursor = pages[(p["type"], p.get("cursor"))]
        return httpx.Response(200, json={"data": data, "meta": {"cursor": cursor} if cursor else {}})

    def run():
        return sync_usage(session, "langfuse", date(2026, 11, 18), httpx.Client(transport=httpx.MockTransport(handler)))

    first = run()
    assert (first.accepted, first.duplicates) == (4, 0)  # o4 has no model, so it's skipped
    assert calls == [("GENERATION", None), ("GENERATION", "c1"), ("GENERATION", "c2"), ("EMBEDDING", None)]
    assert run().duplicates == 4

    ev, costs = _events(session), _costs(session)
    mini = ev["langfuse:o1"]
    assert (mini.provider, mini.model, mini.source, mini.operation) == ("openai", "gpt-4o-mini", "langfuse", "chat")
    # The Langfuse project name is the Burnrate project; the trace name is the feature.
    assert (mini.project, mini.feature, mini.environment, mini.customer_id, mini.user_id) == \
        ("Support App", "support-chat", "production", "acme-corp", "user-7")
    assert (mini.trace_id, mini.latency_ms) == ("a" * 32, 800)
    assert mini.attributes["trace_url"] == "https://langfuse.acme.dev/project/proj1/traces/" + "a" * 32
    assert costs[mini.request_id].method == "priced"

    claude = ev["langfuse:o2"]
    assert (claude.provider, claude.project, claude.feature, claude.team, claude.environment) == \
        ("anthropic", "agents", "planner", "ml", "staging")
    assert (claude.input_tokens, claude.cached_input_tokens, claude.output_tokens) == (50, 1000, 80)

    llama = ev["langfuse:o3"]
    assert (llama.provider, llama.status) == ("unknown", "error")
    assert (costs[llama.request_id].method, costs[llama.request_id].total_cost_usd) == ("provider_reported", 0.01)

    emb = ev["langfuse:e1"]
    assert (emb.operation, emb.feature, emb.input_tokens) == ("embedding", "embed-docs", 5000)


def test_langfuse_v3_is_reported_clearly(session, monkeypatch):
    from burnrate.connectors.langfuse import LangfuseVersionError

    monkeypatch.setattr(get_settings(), "langfuse_public_key", "pk")
    monkeypatch.setattr(get_settings(), "langfuse_secret_key", "sk")

    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/api/public/projects":
            return httpx.Response(200, json={"data": []})
        return httpx.Response(404, json={"message": "Not found"})

    with pytest.raises(LangfuseVersionError, match="v4"):
        sync_usage(session, "langfuse", date(2026, 11, 18), httpx.Client(transport=httpx.MockTransport(handler)))


def test_imported_calls_count_towards_reconciliation(session, monkeypatch):
    """The point of importing: gateway calls are checked against the provider's real bill."""
    from burnrate.models import BilledCost
    from burnrate.services.reconciliation import reconcile

    monkeypatch.setattr(get_settings(), "litellm_url", "http://litellm:4000")
    monkeypatch.setattr(get_settings(), "litellm_api_key", "sk")
    day = datetime.now(UTC).date() - timedelta(days=1)

    def handler(req: httpx.Request) -> httpx.Response:
        data = [_spend_log("chatcmpl-r", startTime=f"{day.isoformat()}T12:00:00", prompt_tokens=1_000_000,
                           completion_tokens=0, metadata={})] if req.url.params["start_date"].startswith(
            day.isoformat()) and req.url.params["page"] == "1" else []
        return httpx.Response(200, json={"data": data, "total_pages": 1, "total_is_capped": False})

    sync_usage(session, "litellm", day, httpx.Client(transport=httpx.MockTransport(handler)))
    session.add(BilledCost(provider="openai", day=day, line_item="gpt-4o", amount_usd=2.75, source="api"))
    session.commit()
    report = json.loads(json.dumps(reconcile(session, days=7), default=str))
    row = next(r for r in report["items"] if r["provider"] == "openai")
    assert (row["estimated_usd"], row["billed_usd"], row["status"]) == (2.5, 2.75, "investigate")  # 10% over
