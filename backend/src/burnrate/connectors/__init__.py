"""Billing connectors: pull what providers actually charged into `BilledCost` for reconciliation.

These use organization *admin* keys and read-only cost endpoints; they never see prompts.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from burnrate.config import get_settings
from burnrate.connectors.anthropic import fetch_anthropic_costs
from burnrate.connectors.openai import fetch_openai_costs
from burnrate.models import BilledCost

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
