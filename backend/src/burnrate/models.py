"""Database models.

Design rule: raw usage (`UsageEvent`) is immutable. Cost is derived data stored separately in
`EventCost`, tagged with the price entry used, so pricing corrections can be re-applied without
touching what was observed. Provider-billed amounts live in `BilledCost` for reconciliation.
"""

from datetime import UTC, date, datetime

from sqlalchemy import (
    JSON,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from burnrate.db import Base


def utcnow() -> datetime:
    return datetime.now(UTC)


class ApiKey(Base):
    __tablename__ = "api_keys"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100))
    prefix: Mapped[str] = mapped_column(String(16), index=True)
    key_hash: Mapped[str] = mapped_column(String(64), unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class UsageEvent(Base):
    __tablename__ = "usage_events"
    __table_args__ = (
        UniqueConstraint("request_id", name="uq_usage_events_request_id"),
        Index("ix_usage_events_day_provider", "day", "provider"),
        Index("ix_usage_events_day_project", "day", "project"),
        Index("ix_usage_events_timestamp", "timestamp"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    request_id: Mapped[str | None] = mapped_column(String(128))
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    day: Mapped[date] = mapped_column(Date)

    provider: Mapped[str] = mapped_column(String(50))
    model: Mapped[str] = mapped_column(String(120), index=True)
    operation: Mapped[str] = mapped_column(String(30), default="chat")

    # Attribution dimensions
    project: Mapped[str] = mapped_column(String(100), default="default")
    feature: Mapped[str | None] = mapped_column(String(100))
    team: Mapped[str | None] = mapped_column(String(100))
    environment: Mapped[str] = mapped_column(String(30), default="production")
    customer_id: Mapped[str | None] = mapped_column(String(100))
    user_id: Mapped[str | None] = mapped_column(String(100))
    trace_id: Mapped[str | None] = mapped_column(String(64))

    # Normalized token counts (see docs/event-schema.md):
    #   input_tokens        uncached prompt tokens
    #   cached_input_tokens prompt tokens served from cache (cache reads)
    #   cache_write_tokens  prompt tokens written to cache
    #   output_tokens       completion tokens, INCLUDING reasoning tokens
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cached_input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cache_write_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    reasoning_tokens: Mapped[int] = mapped_column(Integer, default=0)

    latency_ms: Mapped[int | None] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(20), default="success")
    source: Mapped[str] = mapped_column(String(20), default="sdk")
    provider_reported_cost_usd: Mapped[float | None] = mapped_column(Float)
    attributes: Mapped[dict] = mapped_column(JSON, default=dict)
    ingested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    cost: Mapped["EventCost | None"] = relationship(back_populates="event", uselist=False)

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.cached_input_tokens + self.cache_write_tokens + self.output_tokens


class PriceEntry(Base):
    __tablename__ = "price_entries"
    __table_args__ = (UniqueConstraint("provider", "model", "effective_from", name="uq_price_version"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    provider: Mapped[str] = mapped_column(String(50))
    model: Mapped[str] = mapped_column(String(120))
    # USD per 1M tokens
    input_per_mtok: Mapped[float] = mapped_column(Float)
    output_per_mtok: Mapped[float] = mapped_column(Float, default=0.0)
    cached_input_per_mtok: Mapped[float | None] = mapped_column(Float)
    cache_write_per_mtok: Mapped[float | None] = mapped_column(Float)
    effective_from: Mapped[date] = mapped_column(Date)


class EventCost(Base):
    __tablename__ = "event_costs"
    __table_args__ = (Index("ix_event_costs_day", "day"),)

    event_id: Mapped[int] = mapped_column(ForeignKey("usage_events.id", ondelete="CASCADE"), primary_key=True)
    day: Mapped[date] = mapped_column(Date)
    price_entry_id: Mapped[int | None] = mapped_column(ForeignKey("price_entries.id"))
    input_cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    cached_input_cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    cache_write_cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    output_cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    total_cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    # "priced" | "provider_reported" | "unpriced"
    method: Mapped[str] = mapped_column(String(20), default="priced")
    computed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    event: Mapped[UsageEvent] = relationship(back_populates="cost")


class BilledCost(Base):
    """What the provider actually charged, per provider/day (from billing APIs or CSV exports)."""

    __tablename__ = "billed_costs"
    __table_args__ = (UniqueConstraint("provider", "day", "line_item", name="uq_billed_cost"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    provider: Mapped[str] = mapped_column(String(50))
    day: Mapped[date] = mapped_column(Date, index=True)
    line_item: Mapped[str] = mapped_column(String(200), default="total")
    amount_usd: Mapped[float] = mapped_column(Float)
    source: Mapped[str] = mapped_column(String(30), default="api")
    synced_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Budget(Base):
    __tablename__ = "budgets"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    scope_dimension: Mapped[str | None] = mapped_column(String(30))
    scope_value: Mapped[str | None] = mapped_column(String(120))
    monthly_limit_usd: Mapped[float] = mapped_column(Float)
    alert_threshold: Mapped[float] = mapped_column(Float, default=0.8)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Alert(Base):
    """A fired alert. `dedupe_key` makes evaluation idempotent: each condition notifies once."""

    __tablename__ = "alerts"

    id: Mapped[int] = mapped_column(primary_key=True)
    dedupe_key: Mapped[str] = mapped_column(String(200), unique=True)
    kind: Mapped[str] = mapped_column(String(30))  # budget | anomaly
    severity: Mapped[str] = mapped_column(String(10))
    title: Mapped[str] = mapped_column(String(300))
    detail: Mapped[str] = mapped_column(String(1000))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    delivery_error: Mapped[str | None] = mapped_column(String(500))
