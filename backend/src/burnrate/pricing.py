"""Pricing engine: resolve the price entry for (provider, model, time) and compute event cost."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path

import yaml
from sqlalchemy import select
from sqlalchemy.orm import Session

from burnrate.models import EventCost, PriceEntry, UsageEvent

DEFAULT_REGISTRY = Path(__file__).parent / "data" / "pricing.yaml"
MTOK = 1_000_000


@dataclass(frozen=True)
class Price:
    id: int | None
    provider: str
    model: str
    input_per_mtok: float
    output_per_mtok: float
    cached_input_per_mtok: float | None
    cache_write_per_mtok: float | None
    effective_from: date


@dataclass(frozen=True)
class CostBreakdown:
    input_cost_usd: float
    cached_input_cost_usd: float
    cache_write_cost_usd: float
    output_cost_usd: float
    price_entry_id: int | None
    method: str

    @property
    def total_cost_usd(self) -> float:
        return self.input_cost_usd + self.cached_input_cost_usd + self.cache_write_cost_usd + self.output_cost_usd


def load_registry_file(path: Path = DEFAULT_REGISTRY) -> list[dict]:
    raw = yaml.safe_load(path.read_text())
    default_from = date.fromisoformat(str(raw.get("defaults", {}).get("effective_from", "2025-01-01")))
    rows = []
    for provider, models in raw["models"].items():
        for model, p in models.items():
            rows.append(
                {
                    "provider": provider,
                    "model": model,
                    "input_per_mtok": float(p["input"]),
                    "output_per_mtok": float(p.get("output", 0)),
                    "cached_input_per_mtok": p.get("cached_input"),
                    "cache_write_per_mtok": p.get("cache_write"),
                    "effective_from": date.fromisoformat(str(p["effective_from"]))
                    if "effective_from" in p
                    else default_from,
                }
            )
    return rows


def sync_registry(session: Session, path: Path = DEFAULT_REGISTRY) -> int:
    """Insert registry entries that don't exist yet (by provider/model/effective_from). Returns count added."""
    existing = {
        (p.provider, p.model, p.effective_from)
        for p in session.scalars(select(PriceEntry))
    }
    added = 0
    for row in load_registry_file(path):
        key = (row["provider"], row["model"], row["effective_from"])
        if key not in existing:
            session.add(PriceEntry(**row))
            added += 1
    session.commit()
    return added


class PriceBook:
    """In-memory index of price entries. Cheap to build; rebuild after registry changes."""

    def __init__(self, entries: list[PriceEntry]):
        self._by_key: dict[tuple[str, str], list[Price]] = {}
        for e in entries:
            price = Price(
                e.id, e.provider, e.model, e.input_per_mtok, e.output_per_mtok,
                e.cached_input_per_mtok, e.cache_write_per_mtok, e.effective_from,
            )
            self._by_key.setdefault((e.provider, e.model), []).append(price)
        for versions in self._by_key.values():
            versions.sort(key=lambda p: p.effective_from)

    @classmethod
    def from_session(cls, session: Session) -> PriceBook:
        return cls(list(session.scalars(select(PriceEntry))))

    def resolve(self, provider: str, model: str, on: date) -> Price | None:
        provider, model = provider.lower(), model.lower()
        versions = self._by_key.get((provider, model))
        if versions is None:
            # Longest-prefix match handles dated snapshots like "gpt-4o-2024-08-06".
            candidates = [m for (p, m) in self._by_key if p == provider and model.startswith(m)]
            if not candidates:
                return None
            versions = self._by_key[(provider, max(candidates, key=len))]
        applicable = [v for v in versions if v.effective_from <= on]
        return applicable[-1] if applicable else versions[0]


def compute_cost(event: UsageEvent, book: PriceBook) -> CostBreakdown:
    price = book.resolve(event.provider, event.model, event.day)
    if price is None:
        if event.provider_reported_cost_usd is not None:
            return CostBreakdown(0, 0, 0, event.provider_reported_cost_usd, None, "provider_reported")
        return CostBreakdown(0, 0, 0, 0, None, "unpriced")

    cached_rate = price.cached_input_per_mtok if price.cached_input_per_mtok is not None else price.input_per_mtok
    write_rate = price.cache_write_per_mtok if price.cache_write_per_mtok is not None else price.input_per_mtok
    return CostBreakdown(
        input_cost_usd=event.input_tokens * price.input_per_mtok / MTOK,
        cached_input_cost_usd=event.cached_input_tokens * cached_rate / MTOK,
        cache_write_cost_usd=event.cache_write_tokens * write_rate / MTOK,
        output_cost_usd=event.output_tokens * price.output_per_mtok / MTOK,
        price_entry_id=price.id,
        method="priced",
    )


def to_event_cost(event: UsageEvent, breakdown: CostBreakdown) -> EventCost:
    return EventCost(
        event_id=event.id,
        day=event.day,
        price_entry_id=breakdown.price_entry_id,
        input_cost_usd=breakdown.input_cost_usd,
        cached_input_cost_usd=breakdown.cached_input_cost_usd,
        cache_write_cost_usd=breakdown.cache_write_cost_usd,
        output_cost_usd=breakdown.output_cost_usd,
        total_cost_usd=breakdown.total_cost_usd,
        method=breakdown.method,
    )


def reprice(session: Session, since: date | None = None, batch_size: int = 2000) -> int:
    """Recompute EventCost for all events (optionally from a date). Raw events are never modified."""
    book = PriceBook.from_session(session)
    stmt = select(UsageEvent).order_by(UsageEvent.id)
    if since:
        stmt = stmt.where(UsageEvent.day >= since)
    count = 0
    for event in session.scalars(stmt.execution_options(yield_per=batch_size)):
        session.merge(to_event_cost(event, compute_cost(event, book)))
        count += 1
        if count % batch_size == 0:
            session.flush()
    session.commit()
    return count
