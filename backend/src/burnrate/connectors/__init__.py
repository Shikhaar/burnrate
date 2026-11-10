"""Connectors for provider admin APIs. They call only read-only cost and usage endpoints and never
see prompts. The admin keys themselves can manage the provider account; see docs/deploy.md.

- Billing connectors (OpenAI, Anthropic) pull what the provider charged into `BilledCost` for
  reconciliation against Burnrate's token-based estimates.
- Usage connectors (Cursor, GitHub Copilot) pull spend from AI coding tools that apps don't call
  through an SDK, and store it as usage events costed at what the tool charged.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, date, datetime, time

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from burnrate.config import get_settings
from burnrate.connectors.anthropic import fetch_anthropic_costs
from burnrate.connectors.copilot import fetch_copilot_events
from burnrate.connectors.cursor import fetch_cursor_events
from burnrate.connectors.openai import fetch_openai_costs
from burnrate.models import BilledCost
from burnrate.schemas import IngestResult
from burnrate.services.ingest import ingest_events

# Each fetcher yields (day, line_item, amount_usd).
FETCHERS: dict[str, tuple[Callable, str]] = {
    "openai": (fetch_openai_costs, "openai_admin_key"),
    "anthropic": (fetch_anthropic_costs, "anthropic_admin_key"),
}


def sync_billing(session: Session, provider: str, start: date, client: httpx.Client | None = None) -> int:
    fetch, key_field = FETCHERS[provider]
    api_key = getattr(get_settings(), key_field)
    if not api_key:
        raise SystemExit(f"Set BURNRATE_{key_field.upper()} to sync {provider} billing.")

    totals: dict[tuple[date, str], float] = {}
    with client or httpx.Client(timeout=30) as http:
        for day, line_item, amount in fetch(http, api_key, start):
            totals[(day, line_item)] = totals.get((day, line_item), 0.0) + amount

    existing = {
        (b.day, b.line_item): b
        for b in session.scalars(
            select(BilledCost).where(BilledCost.provider == provider, BilledCost.day >= start)
        )
    }
    for (day, line_item), amount in totals.items():
        row = existing.get((day, line_item))
        if row:
            row.amount_usd = round(amount, 6)
        else:
            session.add(BilledCost(provider=provider, day=day, line_item=line_item,
                                   amount_usd=round(amount, 6), source="api"))
    session.commit()
    return len(totals)


USAGE_PROVIDERS = ("cursor", "copilot")
INGEST_CHUNK = 1000


def sync_usage(session: Session, provider: str, start: date, client: httpx.Client | None = None) -> IngestResult:
    """Import usage events from a coding tool's admin API. Safe to re-run: events are idempotent."""
    settings = get_settings()
    with client or httpx.Client(timeout=30) as http:
        if provider == "cursor":
            if not settings.cursor_admin_key:
                raise SystemExit("Set BURNRATE_CURSOR_ADMIN_KEY to sync Cursor usage.")
            events = fetch_cursor_events(http, settings.cursor_admin_key, datetime.combine(start, time(), UTC),
                                         datetime.now(UTC))
            source = "cursor"
        elif provider == "copilot":
            if not (settings.github_token and settings.github_org):
                raise SystemExit("Set BURNRATE_GITHUB_TOKEN and BURNRATE_GITHUB_ORG to sync GitHub Copilot spend.")
            events = fetch_copilot_events(http, settings.github_token, settings.github_org, start)
            source = "github"
        else:
            raise ValueError(f"unknown usage provider: {provider}")

        total = IngestResult(accepted=0, duplicates=0, rejected=0)
        chunk = []
        for event in events:
            chunk.append(event)
            if len(chunk) >= INGEST_CHUNK:
                total = _add(total, ingest_events(session, chunk, source=source))
                chunk = []
        if chunk:
            total = _add(total, ingest_events(session, chunk, source=source))
    return total


def _add(a: IngestResult, b: IngestResult) -> IngestResult:
    return IngestResult(accepted=a.accepted + b.accepted, duplicates=a.duplicates + b.duplicates,
                        rejected=a.rejected + b.rejected)
