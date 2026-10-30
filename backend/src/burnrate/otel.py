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
    return {a["key"]: _attr_value(a.get("value", {})) for a in items or []}


def spans_to_events(payload: dict) -> tuple[list[EventIn], list[IngestError]]:
    events: list[EventIn] = []
    errors: list[IngestError] = []
    idx = -1
    for rs in payload.get("resourceSpans", []):
        resource = _attrs(rs.get("resource", {}).get("attributes", []))
        for ss in rs.get("scopeSpans", []):
            for span in ss.get("spans", []):
                idx += 1
                a = {**resource, **_attrs(span.get("attributes", []))}
                provider = a.get("gen_ai.provider.name") or a.get("gen_ai.system")
                if not provider:
                    continue  # not a GenAI span
                provider = PROVIDER_ALIASES.get(str(provider).lower(), str(provider).lower())
                input_total = int(a.get("gen_ai.usage.input_tokens") or 0)
                cache_read = int(a.get("gen_ai.usage.cache_read_input_tokens") or 0)
                cache_write = int(a.get("gen_ai.usage.cache_creation_input_tokens") or 0)
                start_ns = int(span.get("startTimeUnixNano") or 0)
                end_ns = int(span.get("endTimeUnixNano") or 0)
                status_code = (span.get("status") or {}).get("code")
                try:
                    events.append(EventIn(
                        request_id=a.get("gen_ai.response.id") or f"otel:{span.get('traceId')}:{span.get('spanId')}",
                        timestamp=datetime.fromtimestamp(start_ns / 1e9, UTC) if start_ns else None,
                        provider=provider,
                        model=a.get("gen_ai.response.model") or a.get("gen_ai.request.model") or "unknown",
                        operation=a.get("gen_ai.operation.name") or "chat",
                        project=a.get("burnrate.project") or a.get("service.name") or "default",
                        feature=a.get("burnrate.feature"),
                        team=a.get("burnrate.team"),
                        environment=a.get("deployment.environment.name") or a.get("deployment.environment")
                        or "production",
                        customer_id=a.get("burnrate.customer_id"),
                        user_id=a.get("user.id") or a.get("enduser.id"),
                        trace_id=span.get("traceId"),
                        # OTel input_tokens is the full prompt; split out cache reads/writes.
                        input_tokens=max(0, input_total - cache_read - cache_write),
                        cached_input_tokens=cache_read,
                        cache_write_tokens=cache_write,
                        output_tokens=int(a.get("gen_ai.usage.output_tokens") or 0),
                        latency_ms=(end_ns - start_ns) // 1_000_000 if start_ns and end_ns else None,
                        status="error" if status_code in (2, "STATUS_CODE_ERROR") else "success",
                    ))
                except ValidationError as e:
                    errors.append(IngestError(index=idx, error=str(e.errors()[0]["msg"])))
    return events, errors
