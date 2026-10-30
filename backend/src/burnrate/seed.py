"""Deterministic demo dataset: 90 days of multi-provider LLM traffic for a fictional SaaS company.

Includes realistic patterns the product should surface: weekday seasonality, steady growth, an
agent retry loop (recent spike), heavy uncached prompts, a frontier model on trivial traffic, and a
provider bill that drifts from the estimate.
"""

from __future__ import annotations

import random
import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from types import SimpleNamespace

from sqlalchemy import delete, func, insert, select, text
from sqlalchemy.orm import Session

from burnrate.models import BilledCost, Budget, EventCost, UsageEvent
from burnrate.pricing import PriceBook, compute_cost


@dataclass
class Workload:
    project: str
    feature: str
    team: str
    provider: str
    model: str
    daily_requests: int
    avg_input: int
    avg_output: int
    cache_ratio: float = 0.0
    error_rate: float = 0.01
    operation: str = "chat"
    latency_ms: int = 1500
    reasoning_share: float = 0.0
    environment: str | None = None  # fixed environment (otherwise ~6% staging)


WORKLOADS = [
    Workload("support-agent", "ticket-triage", "cx-platform", "anthropic", "claude-sonnet-4-5", 70, 18_000, 900,
             cache_ratio=0.55, error_rate=0.02, latency_ms=4200),
    Workload("support-agent", "reply-drafting", "cx-platform", "openai", "gpt-5", 55, 6_500, 700,
             cache_ratio=0.3, latency_ms=3800, reasoning_share=0.35),
    Workload("support-agent", "sentiment-tagging", "cx-platform", "anthropic", "claude-opus-4-5", 60, 900, 60,
             latency_ms=1400),
    Workload("rag-search", "answer-synthesis", "search", "openai", "gpt-4.1", 80, 12_000, 600,
             cache_ratio=0.02, latency_ms=2600),
    Workload("rag-search", "query-embedding", "search", "openai", "text-embedding-3-large", 120, 1_200, 0,
             operation="embeddings", latency_ms=180, error_rate=0.002),
    Workload("rag-search", "reranking", "search", "google", "gemini-2.5-flash", 70, 6_000, 120, latency_ms=900),
    Workload("internal-copilot", "code-review", "platform", "anthropic", "claude-opus-4-5", 18, 28_000, 2_400,
             cache_ratio=0.4, latency_ms=9000),
    Workload("internal-copilot", "sql-assistant", "data", "google", "gemini-2.5-pro", 30, 9_000, 800,
             latency_ms=5200, reasoning_share=0.4),
    Workload("content-studio", "blog-generation", "growth", "openai", "gpt-5", 14, 3_000, 3_500,
             latency_ms=21000, reasoning_share=0.3),
    Workload("content-studio", "image-captions", "growth", "google", "gemini-2.5-flash-lite", 60, 1_500, 120,
             latency_ms=700),
    Workload("data-extraction", "invoice-parsing", "finance-ops", "anthropic", "claude-haiku-4-5", 90, 4_000, 500,
             error_rate=0.03, latency_ms=2100),
    # Coding agents (Claude Code telemetry), attributed per engineer.
    Workload("claude-code", "claude-code:main", "platform", "anthropic", "claude-opus-4-5", 55, 42_000, 1_400,
             cache_ratio=0.85, error_rate=0.01, operation="coding_agent", latency_ms=12000, environment="development"),
    Workload("claude-code", "claude-code:auxiliary", "platform", "anthropic", "claude-haiku-4-5", 40, 3_000, 150,
             operation="coding_agent", latency_ms=900, environment="development"),
    Workload("data-extraction", "contract-review", "finance-ops", "openai", "o3", 8, 22_000, 3_000,
             latency_ms=34000, reasoning_share=0.6),
]

CUSTOMERS = ["acme-corp", "globex", "initech", "umbrella", "hooli", "stark-ind", "wayne-ent", "soylent",
             "cyberdyne", "tyrell"]
CUSTOMER_WEIGHTS = [30, 18, 12, 10, 8, 7, 6, 4, 3, 2]
ENGINEERS = ["priya@example.dev", "marco@example.dev", "aisha@example.dev", "chen@example.dev",
             "sam@example.dev", "lena@example.dev", "omar@example.dev", "julia@example.dev"]
ENGINEER_WEIGHTS = [26, 18, 14, 12, 10, 8, 7, 5]
CUSTOMER_FACING = {"support-agent", "rag-search", "data-extraction"}
BILLING_DRIFT = {"openai": 1.011, "anthropic": 1.004, "google": 1.047}

DEMO_BUDGETS = [
    ("Company AI spend", None, None, 3_400.0, 0.8),
    ("Support agent", "project", "support-agent", 780.0, 0.8),
    ("Search team", "team", "search", 380.0, 0.85),
    ("Growth experiments", "team", "growth", 150.0, 0.75),
]


def _multiplier(w: Workload, day: date, days_ago: int) -> float:
    m = 1.0 + 0.004 * (90 - days_ago)  # ~40% growth across the window
    if day.weekday() >= 5 and w.team not in {"cx-platform"}:
        m *= 0.45
    if w.feature == "ticket-triage" and days_ago <= 2:
        m *= 3.4  # agent retry loop shipped 3 days ago
    if w.feature == "blog-generation" and days_ago in (11, 12):
        m *= 6.0  # bulk campaign job run against the frontier model
    return m


def generate(session: Session, days: int = 90, seed: int = 7, reset: bool = True, scale: float = 4.0) -> dict:
    rng = random.Random(seed)
    if reset:
        for model in (EventCost, UsageEvent, BilledCost, Budget):
            session.execute(delete(model))
        session.commit()

    book = PriceBook.from_session(session)
    next_id = (session.scalar(select(func.max(UsageEvent.id))) or 0) + 1
    now = datetime.now(UTC)
    end = now.date()
    events, costs = [], []
    billed: dict[tuple[str, date], float] = {}

    for days_ago in range(days - 1, -1, -1):
        day = end - timedelta(days=days_ago)
        for w in WORKLOADS:
            base = w.daily_requests * scale
            n = max(0, int(rng.gauss(base, base * 0.12) * _multiplier(w, day, days_ago)))
            for _ in range(n):
                ts = datetime.combine(day, time(), UTC) + timedelta(seconds=rng.randint(0, 86_399))
                if ts > now:
                    continue
                prompt = max(50, int(rng.lognormvariate(0, 0.45) * w.avg_input))
                cached = int(prompt * w.cache_ratio * rng.uniform(0.7, 1.0))
                output = max(0, int(rng.lognormvariate(0, 0.5) * w.avg_output)) if w.avg_output else 0
                failed = rng.random() < w.error_rate
                if failed:
                    output = int(output * rng.uniform(0, 0.3))
                ev = {
                    "id": next_id,
                    "request_id": f"demo_{uuid.UUID(int=rng.getrandbits(128)).hex[:24]}",
                    "timestamp": ts,
                    "day": day,
                    "provider": w.provider,
                    "model": w.model,
                    "operation": w.operation,
                    "project": w.project,
                    "feature": w.feature,
                    "team": w.team,
                    "environment": w.environment or ("staging" if rng.random() < 0.06 else "production"),
                    "customer_id": rng.choices(CUSTOMERS, CUSTOMER_WEIGHTS)[0]
                    if w.project in CUSTOMER_FACING else None,
                    "user_id": rng.choices(ENGINEERS, ENGINEER_WEIGHTS)[0] if w.project == "claude-code" else None,
                    "trace_id": None,
                    "input_tokens": prompt - cached,
                    "cached_input_tokens": cached,
                    "cache_write_tokens": int(cached * 0.08) if w.provider == "anthropic" and cached else 0,
                    "output_tokens": output,
                    "reasoning_tokens": int(output * w.reasoning_share),
                    "latency_ms": int(rng.lognormvariate(0, 0.35) * w.latency_ms),
                    "status": rng.choice(["error", "timeout"]) if failed else "success",
                    "source": "sdk",
                    "provider_reported_cost_usd": None,
                    "attributes": {},
                    "ingested_at": ts,
                }
                c = compute_cost(SimpleNamespace(**ev), book)
                events.append(ev)
                costs.append({
                    "event_id": next_id, "day": day, "price_entry_id": c.price_entry_id,
                    "input_cost_usd": c.input_cost_usd, "cached_input_cost_usd": c.cached_input_cost_usd,
                    "cache_write_cost_usd": c.cache_write_cost_usd, "output_cost_usd": c.output_cost_usd,
                    "total_cost_usd": c.total_cost_usd, "method": c.method, "computed_at": now,
                })
                key = (w.provider, day)
                billed[key] = billed.get(key, 0.0) + c.total_cost_usd
                next_id += 1

    for i in range(0, len(events), 5_000):
        session.execute(insert(UsageEvent), events[i : i + 5_000])
        session.execute(insert(EventCost), costs[i : i + 5_000])
    if session.get_bind().dialect.name == "postgresql" and events:
        # Explicit ids bypass the serial sequence; move it past them so live ingestion doesn't collide.
        session.execute(text("SELECT setval(pg_get_serial_sequence('usage_events', 'id'), :v)"), {"v": next_id - 1})

    # Provider billing data lags ~1 day.
    session.execute(insert(BilledCost), [
        {"provider": p, "day": d, "line_item": "total", "source": "demo",
         "amount_usd": round(v * BILLING_DRIFT.get(p, 1.0) * rng.uniform(0.995, 1.005), 2), "synced_at": now}
        for (p, d), v in billed.items() if d < end
    ])
    if reset:
        session.add_all(
            Budget(name=n, scope_dimension=dim, scope_value=val, monthly_limit_usd=lim, alert_threshold=t)
            for n, dim, val, lim, t in DEMO_BUDGETS
        )
    session.commit()
    return {"events": len(events), "estimated_cost_usd": round(sum(c["total_cost_usd"] for c in costs), 2)}
