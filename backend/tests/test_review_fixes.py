"""Regression tests for bugs found in code review."""

from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy import select

from burnrate.models import EventCost, UsageEvent
from burnrate.pricing import PriceBook
from burnrate.schemas import EventIn
from burnrate.services.ingest import ingest_events


def ev(**kw) -> EventIn:
    return EventIn(**{"provider": "openai", "model": "gpt-4o", "input_tokens": 1000, **kw})


def test_concurrent_batches_with_shared_request_id_keep_their_other_events(session):
    """Two batches race on the same request_id: the loser must not lose its unrelated events."""
    ingest_events(session, [ev(request_id="shared"), ev(request_id="a-only")])
    # Simulate batch B having done its duplicate pre-check before A committed: insert directly.
    from burnrate.services import ingest as ingest_mod

    real_select = ingest_mod.select
    ingest_mod.select = lambda *a, **k: real_select(*a, **k).where(False)  # pre-check sees nothing
    try:
        result = ingest_events(session, [ev(request_id="shared"), ev(request_id="b-only")])
    finally:
        ingest_mod.select = real_select
    assert result.accepted == 1 and result.duplicates == 1
    ids = set(session.scalars(select(UsageEvent.request_id)))
    assert ids == {"shared", "a-only", "b-only"}
    assert session.query(EventCost).count() == 3


def test_blank_request_ids_are_not_idempotency_keys(session):
    result = ingest_events(session, [ev(request_id=""), ev(request_id="  "), ev(request_id="")])
    assert result.accepted == 3
    assert ingest_events(session, [ev(request_id="")]).accepted == 1


def test_offset_timestamps_are_stored_as_utc(session):
    ingest_events(session, [ev(request_id="tz", timestamp="2026-10-30T22:00:00-05:00")])
    e = session.scalar(select(UsageEvent))
    assert e.day == date(2026, 10, 31)
    assert e.timestamp.replace(tzinfo=None) == datetime(2026, 10, 31, 3, 0)


@pytest.mark.parametrize(("model", "expected"), [
    ("gpt-4o-2024-08-06", "gpt-4o"),
    ("claude-sonnet-4-5-20250929", "claude-sonnet-4-5"),
    ("gpt-4o-mini", "gpt-4o-mini"),
    ("o3-mini", None),            # not o3
    ("gpt-5-pro", None),          # not gpt-5
    ("claude-sonnet-4-6", None),  # not claude-sonnet-4
    ("gpt-4o-realtime-preview", None),
])
def test_only_snapshot_suffixes_inherit_a_price(session, model, expected):
    price = PriceBook.from_session(session).resolve("openai" if not model.startswith("claude") else "anthropic",
                                                    model, date(2026, 1, 1))
    assert (price.model if price else None) == expected


def test_forecast_not_dragged_down_by_zero_spend_weekends(session):
    from burnrate.services.forecast import forecast_month

    on = date(2026, 6, 10)  # Wednesday
    for i in range(1, 29):
        d = on - timedelta(days=i)
        if d.weekday() >= 5:
            continue
        e = UsageEvent(timestamp=datetime(d.year, d.month, d.day, 12, tzinfo=UTC), day=d, provider="openai",
                       model="gpt-4o")
        session.add(e)
        session.flush()
        session.add(EventCost(event_id=e.id, day=d, total_cost_usd=10.0))
    session.commit()
    fc = forecast_month(session, on=on)
    future = [p["forecast_usd"] for p in fc["daily"] if p["forecast_usd"] is not None]
    weekday_preds = [v for v in future if v > 0]
    assert weekday_preds and all(v == pytest.approx(10.0, rel=0.05) for v in weekday_preds)


def test_malformed_otlp_span_is_reported_not_fatal(client, api_key):
    now_ns = str(int(datetime.now(UTC).timestamp() * 1e9))
    good = {"traceId": "t", "spanId": "1", "startTimeUnixNano": now_ns, "attributes": [
        {"key": "gen_ai.system", "value": {"stringValue": "openai"}},
        {"key": "gen_ai.request.model", "value": {"stringValue": "gpt-4o"}},
        {"key": "gen_ai.usage.prompt_tokens", "value": {"intValue": "100"}}]}
    bad = {"traceId": "t", "spanId": "2", "attributes": [
        {"key": "gen_ai.system", "value": {"stringValue": "openai"}},
        {"value": {"stringValue": "no key"}},
        {"key": "gen_ai.usage.input_tokens", "value": {"stringValue": "n/a"}}]}
    no_ids = [{"attributes": [{"key": "gen_ai.system", "value": {"stringValue": "openai"}},
                              {"key": "gen_ai.request.model", "value": {"stringValue": "gpt-4o"}}]}] * 2
    payload = {"resourceSpans": [{"scopeSpans": [{"spans": [good, bad, *no_ids]}]}]}
    r = client.post("/v1/otlp/v1/traces", json=payload, headers={"X-API-Key": api_key})
    assert r.status_code == 202
    body = r.json()
    assert body["accepted"] == 3 and body["rejected"] == 1 and body["errors"][0]["index"] == 1
    assert client.get("/api/events?limit=5").json()["total"] == 3


def test_non_ascii_dashboard_token_is_401_not_500(client, monkeypatch):
    from burnrate.config import get_settings

    monkeypatch.setattr(get_settings(), "dashboard_token", "s3cret")
    r = client.get("/api/meta", headers={"Authorization": "Bearer sécret".encode("latin-1")})
    assert r.status_code == 401


def test_token_counts_bounded_to_int32(client, api_key):
    r = client.post("/v1/events", json={"events": [{"provider": "openai", "model": "gpt-4o",
                                                    "input_tokens": 3_000_000_000}]},
                    headers={"X-API-Key": api_key})
    assert r.status_code == 422
