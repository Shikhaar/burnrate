"""LiteLLM proxy: import its spend logs as usage events, so Burnrate can check them against the bill.

GET {base}/spend/logs/v2 — Bearer auth with a LiteLLM key allowed to read spend (a proxy admin key,
or better a key for a `proxy_admin_viewer` user, which can't change anything). `start_date` and
`end_date` are UTC `YYYY-MM-DD HH:MM:SS`; pages start at 1 and hold up to 1000 rows. LiteLLM caps the
reported `total` at 10,000, so we query one day at a time and keep paging until a page comes back empty.

Field mapping (see docs/event-schema.md#litellm):
- provider/model: `custom_llm_provider` + `model` (the deployment that actually served the call)
- tokens: LiteLLM reports OpenAI-style counts, where `prompt_tokens` includes cache reads and writes;
  those are in `metadata.additional_usage_values`, so uncached input = prompt - reads - writes
- attribution: `metadata.spend_logs_metadata` keys `project`, `feature`, `team`, `customer_id`,
  `environment` win; otherwise project = key alias, feature = public model name (`model_group`),
  team = team alias, customer = `end_user`, user = `user`; `session_id` is the trace
- `spend` is kept as the reported cost; Burnrate prices the call itself when it knows the model
- LiteLLM cache hits never reach the provider, so they're recorded with their tokens moved to
  attributes and $0
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta

import httpx

from burnrate.connectors.providers import resolve
from burnrate.schemas import EventIn

PATH = "/spend/logs/v2"
PAGE_SIZE = 1000
MAX_PAGES_PER_DAY = 1000  # 1M calls a day; a guard against a server that never returns an empty page


def fetch_litellm_events(http: httpx.Client, base_url: str, api_key: str, start: date,
                         end: date | None = None) -> Iterator[EventIn]:
    end = end or datetime.now(UTC).date()
    url = base_url.rstrip("/") + PATH
    day = start
    while day <= end:
        for page in range(1, MAX_PAGES_PER_DAY + 1):
            resp = http.get(url, headers={"Authorization": f"Bearer {api_key}"}, params={
                "start_date": f"{day.isoformat()} 00:00:00",
                "end_date": f"{day.isoformat()} 23:59:59",
                "page": page,
                "page_size": PAGE_SIZE,
            })
            resp.raise_for_status()
            body = resp.json()
            rows = body.get("data") or []
            for row in rows:
                event = to_event(row)
                if event is not None:
                    yield event
            # The advertised page count is exact unless LiteLLM capped it; then page until empty.
            if not rows or (not body.get("total_is_capped") and page >= int(body.get("total_pages") or 0)):
                break
        day += timedelta(days=1)


def _json(value) -> dict:
    if isinstance(value, dict):
        return value
    if isinstance(value, str) and value:
        try:
            parsed = json.loads(value)
        except ValueError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def _ts(value) -> datetime | None:
    if not value:
        return None
    ts = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return ts if ts.tzinfo else ts.replace(tzinfo=UTC)  # LiteLLM stores UTC without an offset


def _int(value) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def _clip(value, n: int = 100) -> str | None:
    return str(value)[:n] if value not in (None, "") else None


def _operation(call_type: str) -> str:
    call_type = (call_type or "").lower()
    if "embedding" in call_type:
        return "embedding"
    if "image" in call_type:
        return "image"
    if "transcription" in call_type or "speech" in call_type:
        return "audio"
    return "chat"


def to_event(row: dict) -> EventIn | None:
    if not row.get("request_id"):
        return None
    meta = _json(row.get("metadata"))
    custom = _json(meta.get("spend_logs_metadata"))
    usage = _json(meta.get("additional_usage_values"))
    usage_object = _json(meta.get("usage_object"))

    provider, model = resolve(row.get("model") or row.get("model_group") or "unknown", row.get("custom_llm_provider"))
    reads = _int(usage.get("cache_read_input_tokens"))
    writes = _int(usage.get("cache_creation_input_tokens"))
    prompt = _int(row.get("prompt_tokens"))
    output = _int(row.get("completion_tokens"))
    reasoning = _int(_json(usage_object.get("completion_tokens_details")).get("reasoning_tokens"))

    attributes = {k: v for k, v in {
        "litellm_model_group": row.get("model_group"),
        "litellm_key_alias": meta.get("user_api_key_alias"),
        "litellm_tags": row.get("request_tags") if isinstance(row.get("request_tags"), list) else None,
        "litellm_api_base": row.get("api_base"),
    }.items() if v not in (None, "", [])}

    cache_hit = str(row.get("cache_hit") or "").lower() == "true"
    if cache_hit:
        # Served from LiteLLM's own cache: no provider call, so nothing to bill.
        attributes["litellm_cache_hit"] = True
        attributes["litellm_cached_tokens"] = {"prompt": prompt, "completion": output}
        prompt = reads = writes = output = reasoning = 0

    start, end = _ts(row.get("startTime")), _ts(row.get("endTime"))
    latency = row.get("request_duration_ms")
    if latency is None and start and end:
        latency = int((end - start).total_seconds() * 1000)
    status = str(row.get("status") or "success").lower()

    return EventIn(
        request_id=f"litellm:{row['request_id']}"[:128],
        timestamp=start,
        provider=provider[:50],
        model=(model or "unknown")[:120],
        operation=_operation(row.get("call_type")),
        project=_clip(custom.get("project") or meta.get("user_api_key_alias")) or "litellm",
        feature=_clip(custom.get("feature") or row.get("model_group")),
        team=_clip(custom.get("team") or meta.get("user_api_key_team_alias") or row.get("team_id")),
        environment=_clip(custom.get("environment"), 30) or "production",
        customer_id=_clip(custom.get("customer_id") or row.get("end_user")),
        user_id=_clip(row.get("user")),
        trace_id=_clip(row.get("session_id"), 64),  # LiteLLM sessions group a run's calls
        input_tokens=max(0, prompt - reads - writes),
        cached_input_tokens=reads,
        cache_write_tokens=writes,
        output_tokens=output,
        reasoning_tokens=reasoning,
        latency_ms=max(0, int(latency)) if latency is not None else None,
        status="success" if status in ("success", "") else "error",
        provider_reported_cost_usd=0.0 if cache_hit else round(max(0.0, float(row.get("spend") or 0)), 8),
        attributes=attributes,
    )
