"""Langfuse: import LLM generations as usage events, so Burnrate can check them against the bill.

GET {host}/api/public/v2/observations — HTTP Basic auth with the project's public key (username) and
secret key (password). Needs Langfuse Cloud or self-hosted Langfuse v4+ (v3 only has the older v1 read
API). We ask for GENERATION and EMBEDDING observations in a bounded `fromStartTime`/`toStartTime`
window, up to 1000 a page, and follow `meta.cursor` until it's gone (a page may be empty while a
cursor is still present). The Langfuse project's name, read once from /api/public/projects, becomes
the Burnrate project unless the generation's metadata names one.

Field mapping (see docs/event-schema.md#langfuse):
- provider: Langfuse doesn't record it, so it's worked out from the model name (or a `provider/`
  prefix on it); unknown providers keep Langfuse's cost
- tokens: Langfuse `usageDetails` are non-overlapping buckets: `input` already excludes cached
  tokens. Cache reads are `input_cached_tokens` / `cache_read_input_tokens`, writes are
  `cache_creation_input_tokens`; other `input_*` and `output_*` buckets (audio, reasoning) are
  added to input and output, which is how providers bill them
- attribution: metadata keys `project`, `feature`, `team`, `customer_id`, `environment` win;
  otherwise feature = trace name (then observation name), environment = Langfuse environment,
  user = Langfuse user id
- `totalCost` is kept as the reported cost; Burnrate prices the call itself when it knows the model
- each event links back to its trace in Langfuse (`attributes.trace_url`), where the prompts are

Scores (`fetch_langfuse_scores`): GET {host}/api/public/v3/scores, up to 100 a page, following
`meta.cursor`. NUMERIC, BOOLEAN and CATEGORICAL scores on a trace, or on one of its observations,
become trace scores; session and experiment scores have no trace and are skipped.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime

import httpx

from burnrate.connectors.providers import resolve
from burnrate.schemas import EventIn, ScoreIn

PATH = "/api/public/v2/observations"
PAGE_SIZE = 1000
FIELDS = "core,basic,model,usage,metadata,metrics,trace_context"
TYPES = {"GENERATION": "chat", "EMBEDDING": "embedding"}
MAX_PAGES = 100_000

CACHE_READ_KEYS = ("input_cached_tokens", "cache_read_input_tokens", "input_cache_read")
CACHE_WRITE_KEYS = ("cache_creation_input_tokens", "input_cache_creation", "input_cache_write")
REASONING_KEYS = ("output_reasoning_tokens", "reasoning_tokens", "output_reasoning")


class LangfuseVersionError(RuntimeError):
    pass


def fetch_langfuse_events(http: httpx.Client, host: str, public_key: str, secret_key: str,
                          start: datetime, end: datetime | None = None) -> Iterator[EventIn]:
    base = host.rstrip("/")
    auth = (public_key, secret_key)
    end = end or datetime.now(UTC)
    project_name = _project_name(http, base, auth)
    for obs_type, operation in TYPES.items():
        params: dict = {"type": obs_type, "fields": FIELDS, "limit": PAGE_SIZE,
                        "fromStartTime": _iso(start), "toStartTime": _iso(end)}
        for _ in range(MAX_PAGES):
            resp = http.get(base + PATH, params=params, auth=auth)
            if resp.status_code == 404:
                raise LangfuseVersionError(
                    "This Langfuse has no v2 observations API. Burnrate needs Langfuse Cloud or self-hosted v4+.")
            resp.raise_for_status()
            body = resp.json()
            for obs in body.get("data") or []:
                event = to_event(obs, project_name, operation, base)
                if event is not None:
                    yield event
            cursor = (body.get("meta") or {}).get("cursor")
            if not cursor:
                break
            params["cursor"] = cursor


def _project_name(http: httpx.Client, base: str, auth: tuple[str, str]) -> str:
    resp = http.get(base + "/api/public/projects", auth=auth)
    resp.raise_for_status()
    projects = resp.json().get("data") or []
    name = projects[0].get("name") if projects else None
    return str(name)[:100] if name else "langfuse"


def _iso(ts: datetime) -> str:
    return ts.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _ts(value) -> datetime | None:
    if not value:
        return None
    ts = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return ts if ts.tzinfo else ts.replace(tzinfo=UTC)


def _int(value) -> int:
    try:
        return max(0, int(float(value or 0)))
    except (TypeError, ValueError):
        return 0


def _clip(value, n: int = 100) -> str | None:
    return str(value)[:n] if value not in (None, "") else None


def split_usage(usage: dict) -> dict[str, int]:
    """Langfuse's exclusive usage buckets -> Burnrate's input / cached / cache-write / output / reasoning."""
    usage = {str(k): _int(v) for k, v in (usage or {}).items()}
    reads = sum(usage.get(k, 0) for k in CACHE_READ_KEYS)
    writes = sum(usage.get(k, 0) for k in CACHE_WRITE_KEYS)
    reasoning = sum(usage.get(k, 0) for k in REASONING_KEYS)
    known = {"input", "output", "total", *CACHE_READ_KEYS, *CACHE_WRITE_KEYS}
    other_input = sum(v for k, v in usage.items() if k.startswith("input_") and k not in known)
    other_output = sum(v for k, v in usage.items() if k.startswith("output_") and k not in known)
    return {
        "input_tokens": usage.get("input", 0) + other_input,
        "cached_input_tokens": reads,
        "cache_write_tokens": writes,
        "output_tokens": usage.get("output", 0) + other_output,  # reasoning is billed as output
        "reasoning_tokens": reasoning,
    }


def to_event(obs: dict, project_name: str = "langfuse", operation: str = "chat",
             host: str | None = None) -> EventIn | None:
    if not obs.get("id") or not obs.get("model"):
        return None  # without a model there's nothing to price or attribute
    meta = obs.get("metadata") if isinstance(obs.get("metadata"), dict) else {}
    provider, model = resolve(obs["model"], meta.get("provider") if isinstance(meta.get("provider"), str) else None)
    tokens = split_usage(obs.get("usageDetails") or {})
    cost = obs.get("totalCost")
    latency = obs.get("latency")
    environment = meta.get("environment") or obs.get("environment")
    if environment in (None, "", "default"):
        environment = "production"

    return EventIn(
        request_id=f"langfuse:{obs['id']}"[:128],
        timestamp=_ts(obs.get("startTime")),
        provider=provider[:50],
        model=model[:120],
        operation=operation,
        project=_clip(meta.get("project")) or project_name,
        feature=_clip(meta.get("feature") or obs.get("traceName") or obs.get("name")),
        team=_clip(meta.get("team")),
        environment=str(environment)[:30],
        customer_id=_clip(meta.get("customer_id") or meta.get("customerId")),
        user_id=_clip(obs.get("userId")),
        trace_id=_clip(obs.get("traceId"), 64),
        **tokens,
        latency_ms=int(float(latency) * 1000) if latency is not None else None,
        status="error" if obs.get("level") == "ERROR" else "success",
        provider_reported_cost_usd=round(float(cost), 8) if cost is not None and float(cost) >= 0 else None,
        attributes={k: v for k, v in {
            "langfuse_observation": obs.get("name"),
            "langfuse_session_id": obs.get("sessionId"),
            "langfuse_tags": obs.get("tags") or None,
            "langfuse_release": obs.get("release"),
            "langfuse_prompt": obs.get("promptName"),
            "trace_url": f"{host}/project/{obs['projectId']}/traces/{obs['traceId']}"
            if host and obs.get("projectId") and obs.get("traceId") else None,
        }.items() if v not in (None, "")},
    )


SCORES_PATH = "/api/public/v3/scores"
SCORE_TYPES = "NUMERIC,BOOLEAN,CATEGORICAL"


def fetch_langfuse_scores(http: httpx.Client, host: str, public_key: str, secret_key: str,
                          start: datetime, end: datetime | None = None) -> Iterator[ScoreIn]:
    base = host.rstrip("/")
    params: dict = {"fields": "subject", "dataType": SCORE_TYPES, "limit": 100,
                    "fromTimestamp": _iso(start), "toTimestamp": _iso(end or datetime.now(UTC))}
    for _ in range(MAX_PAGES):
        resp = http.get(base + SCORES_PATH, params=params, auth=(public_key, secret_key))
        if resp.status_code == 404:
            raise LangfuseVersionError(
                "This Langfuse has no v3 scores API. Burnrate needs Langfuse Cloud or self-hosted v4+.")
        resp.raise_for_status()
        body = resp.json()
        for score in body.get("data") or []:
            item = to_score(score)
            if item is not None:
                yield item
        cursor = (body.get("meta") or {}).get("cursor")
        if not cursor:
            return
        params["cursor"] = cursor


def to_score(score: dict) -> ScoreIn | None:
    subject = score.get("subject") or {}
    kind = subject.get("kind")
    trace_id = subject.get("id") if kind == "trace" else subject.get("traceId") if kind == "observation" else None
    if not trace_id or not score.get("id") or not score.get("name"):
        return None
    data_type, value = score.get("dataType"), score.get("value")
    if data_type == "CATEGORICAL":
        if not value:
            return None
        return ScoreIn(id=str(score["id"]), trace_id=str(trace_id)[:64], name=str(score["name"])[:100],
                       label=str(value)[:100], timestamp=_ts(score.get("timestamp")))
    if data_type in ("NUMERIC", "BOOLEAN") and value is not None:
        return ScoreIn(id=str(score["id"]), trace_id=str(trace_id)[:64], name=str(score["name"])[:100],
                       value=bool(value) if data_type == "BOOLEAN" else float(value),
                       timestamp=_ts(score.get("timestamp")))
    return None
