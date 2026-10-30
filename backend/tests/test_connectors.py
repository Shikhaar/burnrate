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
