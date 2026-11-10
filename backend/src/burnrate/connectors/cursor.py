"""Cursor Admin API: per-request usage events for a Cursor team.

POST https://api.cursor.com/teams/filtered-usage-events — HTTP Basic auth with the admin API key as
the username. `startDate`/`endDate` are epoch milliseconds (inclusive); pages are 1-indexed, up to
1000 events each. Cursor aggregates usage hourly and allows 60 requests/minute per team.

Each item becomes one `coding_agent` usage event under provider `cursor`. Cost is what Cursor
charged (`chargedCents`, which includes the Cursor token fee) and falls back to the model cost in
`tokenUsage.totalCents`. Requests included in the plan are recorded with their tokens and $0.
Cursor gives events no id, so the idempotency key is a hash of the event's identifying fields.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterator
from datetime import UTC, datetime

import httpx

from burnrate.schemas import EventIn

URL = "https://api.cursor.com/teams/filtered-usage-events"
PAGE_SIZE = 1000


def fetch_cursor_events(http: httpx.Client, api_key: str, start: datetime, end: datetime) -> Iterator[EventIn]:
    body = {"startDate": int(start.timestamp() * 1000), "endDate": int(end.timestamp() * 1000),
            "page": 1, "pageSize": PAGE_SIZE}
    while True:
        resp = http.post(URL, json=body, auth=(api_key, ""))
        resp.raise_for_status()
        data = resp.json()
        for item in data.get("usageEvents", []):
            yield to_event(item)
        if not (data.get("pagination") or {}).get("hasNextPage"):
            return
        body["page"] += 1


def _feature(item: dict) -> str:
    if item.get("prNumber") is not None or item.get("prAuthor"):
        return "cursor:bugbot"
    if item.get("automationId"):
        return "cursor:automation"
    if item.get("cloudAgentId"):
        return "cursor:cloud-agent"
    return "cursor"


def to_event(item: dict) -> EventIn:
    tokens = item.get("tokenUsage") or {}
    ts_ms = int(item["timestamp"])
    user = item.get("userEmail") or item.get("serviceAccountName") or item.get("serviceAccountId")
    if item.get("chargedCents") is not None:
        cost = float(item["chargedCents"]) / 100
    elif tokens.get("totalCents") is not None:
        cost = float(tokens["totalCents"]) / 100
    else:
        cost = None
    identity = "|".join(str(item.get(k, "")) for k in (
        "timestamp", "userEmail", "serviceAccountId", "model", "kind", "conversationId", "cloudAgentId",
        "requestsCosts", "chargedCents"))
    identity += "|" + "|".join(str(tokens.get(k, "")) for k in (
        "inputTokens", "outputTokens", "cacheReadTokens", "cacheWriteTokens"))
    return EventIn(
        request_id="cursor:" + hashlib.sha256(identity.encode()).hexdigest(),
        timestamp=datetime.fromtimestamp(ts_ms / 1000, UTC),
        provider="cursor",
        model=item.get("model") or "unknown",
        operation="coding_agent",
        project="cursor",
        feature=_feature(item),
        environment="development",
        user_id=user,
        # Cursor's tokenUsage reports uncached input separately from cache reads and writes.
        input_tokens=int(tokens.get("inputTokens") or 0),
        cached_input_tokens=int(tokens.get("cacheReadTokens") or 0),
        cache_write_tokens=int(tokens.get("cacheWriteTokens") or 0),
        output_tokens=int(tokens.get("outputTokens") or 0),
        provider_reported_cost_usd=round(cost, 8) if cost is not None else None,
        attributes={k: v for k, v in {
            "kind": item.get("kind"),
            "max_mode": item.get("maxMode"),
            "requests_costs": item.get("requestsCosts"),
            "is_chargeable": item.get("isChargeable"),
            "is_headless": item.get("isHeadless"),
            "repo": item.get("repo"),
            "conversation_id": item.get("conversationId"),
        }.items() if v is not None},
    )
