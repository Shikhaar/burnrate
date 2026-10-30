"""Map OTLP/JSON spans that follow the OpenTelemetry GenAI semantic conventions to usage events.

Recognized span attributes:
  gen_ai.system / gen_ai.provider.name      -> provider
  gen_ai.response.model / gen_ai.request.model -> model
  gen_ai.operation.name                     -> operation
  gen_ai.usage.input_tokens / output_tokens -> tokens
  gen_ai.usage.cache_read_input_tokens / cache_creation_input_tokens (when present)
  burnrate.project / feature / team / customer_id, deployment.environment -> attribution
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime

from pydantic import ValidationError

from burnrate.schemas import EventIn, IngestError

PROVIDER_ALIASES = {"az.ai.openai": "openai", "azure.ai.openai": "openai", "gcp.gemini": "google",
                    "gcp.vertex_ai": "google", "aws.bedrock": "bedrock", "gemini": "google"}


def _attr_value(v: dict):
    for k in ("stringValue", "intValue", "doubleValue", "boolValue"):
        if k in v:
            return int(v[k]) if k == "intValue" else v[k]
    return None


def _attrs(items: list[dict]) -> dict:
    return {a["key"]: _attr_value(a.get("value") or {}) for a in items or [] if isinstance(a, dict) and "key" in a}


def _int(v) -> int:
    return int(float(v)) if v not in (None, "") else 0


# Per-span/datapoint problems we report and skip, instead of failing the whole export.
BAD_INPUT = (ValidationError, ValueError, TypeError, KeyError, OverflowError)


def spans_to_events(payload: dict) -> tuple[list[EventIn], list[IngestError]]:
    events: list[EventIn] = []
    errors: list[IngestError] = []
    idx = -1
    for rs in payload.get("resourceSpans", []):
        resource = _attrs(rs.get("resource", {}).get("attributes", []))
        for ss in rs.get("scopeSpans", []):
            for span in ss.get("spans", []):
                idx += 1
                try:
                    event = _span_to_event(span, resource)
                except BAD_INPUT as e:
                    errors.append(IngestError(index=idx, error=_describe(e)))
                    continue
                if event is not None:
                    events.append(event)
    return events, errors


def _describe(e: Exception) -> str:
    if isinstance(e, ValidationError):
        return str(e.errors()[0]["msg"])
    return f"{type(e).__name__}: {e}"


def _span_to_event(span: dict, resource: dict) -> EventIn | None:
    a = {**resource, **_attrs(span.get("attributes", []))}
    provider = a.get("gen_ai.provider.name") or a.get("gen_ai.system")
    if not provider:
        return None  # not a GenAI span
    provider = PROVIDER_ALIASES.get(str(provider).lower(), str(provider).lower())
    # Current names first, then the older prompt/completion names some instrumentations still emit.
    input_total = _int(a.get("gen_ai.usage.input_tokens") or a.get("gen_ai.usage.prompt_tokens"))
    output = _int(a.get("gen_ai.usage.output_tokens") or a.get("gen_ai.usage.completion_tokens"))
    cache_read = _int(a.get("gen_ai.usage.cache_read_input_tokens") or a.get("gen_ai.usage.cache_read.input_tokens"))
    cache_write = _int(a.get("gen_ai.usage.cache_creation_input_tokens")
                       or a.get("gen_ai.usage.cache_creation.input_tokens"))
    start_ns = _int(span.get("startTimeUnixNano"))
    end_ns = _int(span.get("endTimeUnixNano"))
    status_code = (span.get("status") or {}).get("code")
    trace_id, span_id = span.get("traceId"), span.get("spanId")
    return EventIn(
        request_id=a.get("gen_ai.response.id") or (f"otel:{trace_id}:{span_id}" if trace_id and span_id else None),
        timestamp=datetime.fromtimestamp(start_ns / 1e9, UTC) if start_ns else None,
        provider=provider,
        model=a.get("gen_ai.response.model") or a.get("gen_ai.request.model") or "unknown",
        operation=a.get("gen_ai.operation.name") or "chat",
        project=a.get("burnrate.project") or a.get("service.name") or "default",
        feature=a.get("burnrate.feature"),
        team=a.get("burnrate.team"),
        environment=a.get("deployment.environment.name") or a.get("deployment.environment") or "production",
        customer_id=a.get("burnrate.customer_id"),
        user_id=a.get("user.id") or a.get("enduser.id"),
        trace_id=trace_id,
        # OTel input_tokens is the full prompt; split out cache reads/writes.
        input_tokens=max(0, input_total - cache_read - cache_write),
        cached_input_tokens=cache_read,
        cache_write_tokens=cache_write,
        output_tokens=output,
        latency_ms=(end_ns - start_ns) // 1_000_000 if start_ns and end_ns else None,
        status="error" if status_code in (2, "STATUS_CODE_ERROR") else "success",
    )


# ---- Claude Code (coding-agent) metrics --------------------------------------------------------
# Claude Code exports delta counters `claude_code.token.usage` (attribute `type` = input | output |
# cacheRead | cacheCreation) and `claude_code.cost.usage` (USD), tagged with model, session and user.
# Datapoints sharing (time, session, model, user, query_source) are folded into one usage event.

TOKEN_METRIC = "claude_code.token.usage"
COST_METRIC = "claude_code.cost.usage"
TOKEN_FIELDS = {"input": "input_tokens", "output": "output_tokens", "cacheRead": "cached_input_tokens",
                "cacheCreation": "cache_write_tokens"}
CUMULATIVE = 2


def _number(dp: dict) -> float:
    return float(dp.get("asDouble", dp.get("asInt", 0)) or 0)


def metrics_to_events(payload: dict) -> tuple[list[EventIn], list[IngestError]]:
    groups: dict[tuple, dict] = {}
    errors: list[IngestError] = []
    for rs in payload.get("resourceMetrics", []):
        resource = _attrs(rs.get("resource", {}).get("attributes", []))
        for sm in rs.get("scopeMetrics", []):
            for metric in sm.get("metrics", []):
                name = metric.get("name")
                if name not in (TOKEN_METRIC, COST_METRIC):
                    continue
                agg = metric.get("sum") or {}
                if agg.get("aggregationTemporality") == CUMULATIVE:
                    errors.append(IngestError(index=-1, error=f"{name}: cumulative temporality is not supported; "
                                              "set OTEL_EXPORTER_OTLP_METRICS_TEMPORALITY_PREFERENCE=delta"))
                    continue
                for i, dp in enumerate(agg.get("dataPoints", [])):
                    try:
                        a = {**resource, **_attrs(dp.get("attributes", []))}
                        value = _number(dp)
                        user = a.get("user.email") or a.get("user.account_uuid") or a.get("user.id")
                        key = (dp.get("timeUnixNano"), a.get("session.id"), a.get("model"), user,
                               a.get("query_source"))
                    except BAD_INPUT as e:
                        errors.append(IngestError(index=i, error=f"{name}: {_describe(e)}"))
                        continue
                    g = groups.setdefault(key, {"attrs": a, "tokens": {}, "cost": 0.0})
                    if name == TOKEN_METRIC and a.get("type") in TOKEN_FIELDS:
                        field = TOKEN_FIELDS[a["type"]]
                        g["tokens"][field] = g["tokens"].get(field, 0) + int(value)
                    elif name == COST_METRIC:
                        g["cost"] += value

    events: list[EventIn] = []
    for (ts_ns, session, model, user, source), g in groups.items():
        a = g["attrs"]
        try:
            events.append(EventIn(
                # Hashed so long session ids / emails can't be truncated into collisions.
                request_id="cc:" + hashlib.sha256(f"{session}|{model}|{ts_ns}|{source}|{user}".encode()).hexdigest(),
                timestamp=datetime.fromtimestamp(_int(ts_ns) / 1e9, UTC) if ts_ns else None,
                provider="anthropic",
                model=model or "unknown",
                operation="coding_agent",
                project=a.get("burnrate.project") or "claude-code",
                feature=f"claude-code:{source}" if source else "claude-code",
                team=a.get("burnrate.team") or a.get("team"),
                environment=a.get("deployment.environment") or "development",
                user_id=user,
                trace_id=(session or "")[:64] or None,
                provider_reported_cost_usd=round(g["cost"], 8) if g["cost"] else None,
                attributes={k: a[k] for k in ("service.name", "terminal.type", "app.version") if a.get(k)},
                **g["tokens"],
            ))
        except BAD_INPUT as e:
            errors.append(IngestError(index=-1, error=_describe(e)))
    return events, errors
