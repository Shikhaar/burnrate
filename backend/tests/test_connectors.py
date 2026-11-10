from datetime import date

import httpx
import pytest

from burnrate.config import get_settings
from burnrate.connectors import sync_billing
from burnrate.models import BilledCost


def test_anthropic_cost_report_paginates_and_converts_cents(session, monkeypatch):
    monkeypatch.setattr(get_settings(), "anthropic_admin_key", "sk-ant-admin-test")
    pages = {
        None: {"data": [{"starting_at": "2026-10-01T00:00:00Z", "results": [
            {"amount": "1234.5", "currency": "USD", "description": "Sonnet input"}]}],
            "has_more": True, "next_page": "p2"},
        "p2": {"data": [{"starting_at": "2026-10-02T00:00:00Z", "results": [
            {"amount": "100", "currency": "USD", "description": "Sonnet input"}]}],
            "has_more": False, "next_page": None},
    }

    def handler(req: httpx.Request) -> httpx.Response:
        assert req.headers["x-api-key"] == "sk-ant-admin-test"
        return httpx.Response(200, json=pages[req.url.params.get("page")])

    n = sync_billing(session, "anthropic", date(2026, 10, 1), httpx.Client(transport=httpx.MockTransport(handler)))
    assert n == 2
    rows = {r.day: r.amount_usd for r in session.query(BilledCost)}
    assert rows[date(2026, 10, 1)] == pytest.approx(12.345)
    assert rows[date(2026, 10, 2)] == pytest.approx(1.0)


def test_openai_costs_upserts(session, monkeypatch):
    monkeypatch.setattr(get_settings(), "openai_admin_key", "sk-admin-test")
    amount = {"v": 3.0}

    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": [{"start_time": 1790812800, "results": [
            {"amount": {"value": amount["v"], "currency": "usd"}, "line_item": "gpt-4o, input"}]}],
            "has_more": False})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    sync_billing(session, "openai", date(2026, 10, 1), client)
    amount["v"] = 5.0
    sync_billing(session, "openai", date(2026, 10, 1), httpx.Client(transport=httpx.MockTransport(handler)))
    rows = session.query(BilledCost).all()
    assert len(rows) == 1 and rows[0].amount_usd == pytest.approx(5.0)


# ---- Coding-tool usage connectors -------------------------------------------------------------------
def _cursor_item(ts="1793000000000", **kw):
    return {"timestamp": ts, "userEmail": "dev@acme.io", "model": "claude-4.5-sonnet-thinking",
            "kind": "Usage-based", "maxMode": False, "requestsCosts": 1.4, "isTokenBasedCall": True,
            "isChargeable": True, "isHeadless": False,
            "tokenUsage": {"inputTokens": 1200, "outputTokens": 300, "cacheWriteTokens": 50,
                           "cacheReadTokens": 9000, "totalCents": 4.2},
            "chargedCents": 5.0, **kw}


def test_cursor_usage_events_paginate_and_are_idempotent(session, monkeypatch):
    from burnrate.connectors import sync_usage
    from burnrate.models import EventCost, UsageEvent

    monkeypatch.setattr(get_settings(), "cursor_admin_key", "key_cursor")
    pages = {
        1: [_cursor_item(), _cursor_item(ts="1793000001000", cloudAgentId="bc-1", chargedCents=None)],
        2: [_cursor_item(ts="1793000002000", kind="Included in Business", tokenUsage=None, chargedCents=0,
                         isTokenBasedCall=False, prNumber=42, repo="github.com/acme/api")],
    }
    bodies = []

    def handler(req: httpx.Request) -> httpx.Response:
        import base64
        import json

        assert req.method == "POST"
        assert req.headers["authorization"] == "Basic " + base64.b64encode(b"key_cursor:").decode()
        body = json.loads(req.content)
        bodies.append(body)
        return httpx.Response(200, json={"usageEvents": pages[body["page"]],
                                         "pagination": {"hasNextPage": body["page"] == 1}})

    def run():
        return sync_usage(session, "cursor", date(2026, 10, 1), httpx.Client(transport=httpx.MockTransport(handler)))

    first = run()
    assert (first.accepted, first.duplicates) == (3, 0)
    assert bodies[0]["pageSize"] == 1000 and bodies[0]["startDate"] == 1790812800000
    again = run()
    assert (again.accepted, again.duplicates) == (0, 3)

    events = {e.feature: e for e in session.query(UsageEvent)}
    main = events["cursor"]
    assert main.provider == "cursor" and main.model == "claude-4.5-sonnet-thinking" and main.user_id == "dev@acme.io"
    assert (main.input_tokens, main.cached_input_tokens, main.cache_write_tokens, main.output_tokens) == \
        (1200, 9000, 50, 300)
    assert main.source == "cursor" and main.operation == "coding_agent"
    assert main.attributes["kind"] == "Usage-based"
    costs = {c.event_id: c for c in session.query(EventCost)}
    assert costs[main.id].method == "provider_reported" and costs[main.id].total_cost_usd == pytest.approx(0.05)
    # No chargedCents: fall back to the model cost. Included bugbot request: tokens unknown, $0.
    assert costs[events["cursor:cloud-agent"].id].total_cost_usd == pytest.approx(0.042)
    assert costs[events["cursor:bugbot"].id].total_cost_usd == 0
    assert events["cursor:bugbot"].attributes["repo"] == "github.com/acme/api"


def test_copilot_billing_usage_keeps_finished_copilot_days(session, monkeypatch):
    from burnrate.connectors import copilot, sync_usage
    from burnrate.models import EventCost, UsageEvent

    monkeypatch.setattr(get_settings(), "github_token", "ghp_test")
    monkeypatch.setattr(get_settings(), "github_org", "Acme")
    real_fetch = copilot.fetch_copilot_events
    monkeypatch.setattr("burnrate.connectors.fetch_copilot_events",
                        lambda http, token, org, start: real_fetch(http, token, org, start, today=date(2026, 11, 2)))
    months = []

    def item(day, sku, net, gross=None, product="Copilot", qty=1):
        return {"date": f"{day}T00:00:00Z", "product": product, "sku": sku, "quantity": qty, "unitType": "units",
                "pricePerUnit": 0.04, "grossAmount": gross if gross is not None else net, "discountAmount": 0,
                "netAmount": net, "organizationName": "Acme"}

    def handler(req: httpx.Request) -> httpx.Response:
        assert req.url.path == "/organizations/Acme/settings/billing/usage"
        assert req.headers["authorization"] == "Bearer ghp_test"
        months.append((req.url.params["year"], req.url.params["month"]))
        if req.url.params["month"] == "10":
            return httpx.Response(200, json={"usageItems": [
                item("2026-09-30", "Copilot Business", 99),  # before the start date
                item("2026-10-31", "Copilot Business", 12.67),
                item("2026-10-31", "Copilot Premium Request", 2.0, gross=6.0, qty=150),
                item("2026-10-31", "Copilot Premium Request", 1.0, gross=1.0, qty=25),  # second repo row
                item("2026-10-31", "Actions Linux", 4.0, product="Actions"),
            ]})
        return httpx.Response(200, json={"usageItems": [
            item("2026-11-01", "Copilot Business", 12.67),
            item("2026-11-02", "Copilot Business", 3.0),  # today: incomplete, skipped
        ]})

    result = sync_usage(session, "copilot", date(2026, 10, 1), httpx.Client(transport=httpx.MockTransport(handler)))
    assert months == [("2026", "10"), ("2026", "11")]
    assert result.accepted == 3

    rows = {(e.day, e.model): e for e in session.query(UsageEvent)}
    assert set(rows) == {(date(2026, 10, 31), "copilot business"), (date(2026, 10, 31), "copilot premium request"),
                         (date(2026, 11, 1), "copilot business")}
    premium = rows[(date(2026, 10, 31), "copilot premium request")]
    assert premium.provider == "github-copilot" and premium.operation == "premium_requests"
    assert premium.attributes["quantity"] == 175 and premium.attributes["gross_amount_usd"] == pytest.approx(7.0)
    cost = session.get(EventCost, premium.id)
    assert cost.total_cost_usd == pytest.approx(3.0) and cost.method == "provider_reported"


def test_usage_sync_requires_configuration(session, monkeypatch):
    from burnrate.connectors import sync_usage

    monkeypatch.setattr(get_settings(), "github_token", "")
    with pytest.raises(SystemExit, match="BURNRATE_GITHUB_TOKEN"):
        sync_usage(session, "copilot", date(2026, 10, 1))


def test_reconciliation_skips_tools_billed_at_source(session, monkeypatch):
    from burnrate.schemas import EventIn
    from burnrate.services.ingest import ingest_events
    from burnrate.services.reconciliation import reconcile

    ingest_events(session, [EventIn(provider="cursor", model="gpt-5", provider_reported_cost_usd=1.0),
                            EventIn(provider="openai", model="gpt-4o", input_tokens=1000)], source="cursor")
    providers = {i["provider"] for i in reconcile(session, 7)["items"]}
    assert providers == {"openai"}
