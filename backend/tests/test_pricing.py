from datetime import date
from types import SimpleNamespace

import pytest

from burnrate.models import PriceEntry
from burnrate.pricing import PriceBook, compute_cost


def ev(**kw):
    base = dict(provider="openai", model="gpt-4o", day=date(2026, 6, 1), input_tokens=0, cached_input_tokens=0,
                cache_write_tokens=0, output_tokens=0, provider_reported_cost_usd=None)
    return SimpleNamespace(**{**base, **kw})


def book(*entries):
    return PriceBook([PriceEntry(id=i, **e) for i, e in enumerate(entries, 1)])


GPT4O = dict(provider="openai", model="gpt-4o", input_per_mtok=2.5, output_per_mtok=10.0,
             cached_input_per_mtok=1.25, cache_write_per_mtok=None, effective_from=date(2025, 1, 1))


def test_cost_splits_input_cached_and_output():
    c = compute_cost(ev(input_tokens=1_000_000, cached_input_tokens=1_000_000, output_tokens=500_000), book(GPT4O))
    assert c.input_cost_usd == pytest.approx(2.5)
    assert c.cached_input_cost_usd == pytest.approx(1.25)
    assert c.output_cost_usd == pytest.approx(5.0)
    assert c.total_cost_usd == pytest.approx(8.75)
    assert c.method == "priced"


def test_dated_snapshot_resolves_by_longest_prefix():
    mini = {**GPT4O, "model": "gpt-4o-mini", "input_per_mtok": 0.15}
    b = book(GPT4O, mini)
    assert b.resolve("openai", "gpt-4o-mini-2024-07-18", date(2026, 1, 1)).model == "gpt-4o-mini"
    assert b.resolve("openai", "gpt-4o-2024-08-06", date(2026, 1, 1)).model == "gpt-4o"


def test_price_versions_apply_by_effective_date():
    cut = {**GPT4O, "input_per_mtok": 1.0, "effective_from": date(2026, 3, 1)}
    b = book(GPT4O, cut)
    assert b.resolve("openai", "gpt-4o", date(2026, 2, 28)).input_per_mtok == 2.5
    assert b.resolve("openai", "gpt-4o", date(2026, 3, 1)).input_per_mtok == 1.0


def test_unknown_model_falls_back_to_provider_reported_cost():
    c = compute_cost(ev(model="mystery-1", provider_reported_cost_usd=0.42), book(GPT4O))
    assert c.method == "provider_reported" and c.total_cost_usd == pytest.approx(0.42)
    assert compute_cost(ev(model="mystery-1"), book(GPT4O)).method == "unpriced"


def test_missing_cache_rate_bills_cached_tokens_at_input_rate():
    no_cache = {**GPT4O, "cached_input_per_mtok": None}
    c = compute_cost(ev(cached_input_tokens=1_000_000), book(no_cache))
    assert c.cached_input_cost_usd == pytest.approx(2.5)


def test_same_money_across_dimensions_is_one_incident():
    from burnrate.services.anomalies import group_incidents

    def a(dim, key, cost, base, date="2026-10-29"):
        return {"date": date, "dimension": dim, "key": key, "cost_usd": cost, "baseline_usd": base, "related": []}

    out = group_incidents([
        a("project", "support", 65, 27),     # +38 — same excess as the feature
        a("model", "sonnet", 58, 20),        # +38
        a("feature", "triage", 58, 20),      # +38 -> primary (most specific)
        a("model", "gpt-5", 90, 30),         # +60 — a different incident
        a("feature", "triage", 58, 20, date="2026-10-28"),
    ])
    primary = next(i for i in out if i["key"] == "triage" and i["date"] == "2026-10-29")
    assert sorted(primary["related"]) == ["model:sonnet", "project:support"]
    assert {(i["dimension"], i["key"], i["date"]) for i in out} == {
        ("feature", "triage", "2026-10-29"), ("model", "gpt-5", "2026-10-29"), ("feature", "triage", "2026-10-28")}
