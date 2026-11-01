"""Prometheus metrics.

Exposed at `GET /metrics` in the Prometheus text format. HTTP metrics are labelled by the matched
route template (`/api/breakdown`, not `/api/breakdown?dimension=…`) so label cardinality stays bounded.
"""

from prometheus_client import Counter, Histogram

HTTP_REQUESTS = Counter(
    "burnrate_http_requests_total", "HTTP requests handled.", ["method", "route", "status"]
)
HTTP_LATENCY = Histogram(
    "burnrate_http_request_duration_seconds",
    "HTTP request latency.",
    ["method", "route"],
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10),
)
EVENTS_INGESTED = Counter(
    "burnrate_events_ingested_total", "Usage events received, by outcome.", ["outcome"]
)
RATE_LIMITED = Counter(
    "burnrate_rate_limited_total", "Requests rejected with 429, by limit scope.", ["scope"]
)
ALERTS_FIRED = Counter("burnrate_alerts_fired_total", "New alerts fired.", ["kind", "severity"])
WEBHOOK_DELIVERIES = Counter(
    "burnrate_webhook_deliveries_total", "Alert webhook delivery attempts, by outcome.", ["outcome"]
)


def route_label(scope: dict) -> str:
    route = scope.get("route")
    # Unmatched paths share one label, so scanners probing random URLs can't explode cardinality.
    return getattr(route, "path", None) or "unmatched"
