from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator

Dimension = Literal["provider", "model", "project", "feature", "team", "environment", "customer", "user"]


class EventIn(BaseModel):
    """One LLM call, normalized. See docs/event-schema.md for per-provider mapping."""

    request_id: str | None = Field(None, max_length=128, description="Idempotency key; duplicates are skipped.")
    timestamp: datetime | None = None
    provider: str = Field(..., min_length=1, max_length=50)
    model: str = Field(..., min_length=1, max_length=120)
    operation: str = Field("chat", max_length=30)

    project: str = Field("default", max_length=100)
    feature: str | None = Field(None, max_length=100)
    team: str | None = Field(None, max_length=100)
    environment: str = Field("production", max_length=30)
    customer_id: str | None = Field(None, max_length=100)
    user_id: str | None = Field(None, max_length=100)
    trace_id: str | None = Field(None, max_length=64)

    input_tokens: int = Field(0, ge=0)
    cached_input_tokens: int = Field(0, ge=0)
    cache_write_tokens: int = Field(0, ge=0)
    output_tokens: int = Field(0, ge=0)
    reasoning_tokens: int = Field(0, ge=0)

    latency_ms: int | None = Field(None, ge=0)
    status: Literal["success", "error", "timeout", "cancelled"] = "success"
    provider_reported_cost_usd: float | None = Field(None, ge=0)
    attributes: dict = Field(default_factory=dict)

    @field_validator("provider", "model")
    @classmethod
    def _lower(cls, v: str) -> str:
        return v.strip().lower()

    @field_validator("timestamp")
    @classmethod
    def _tz(cls, v: datetime | None) -> datetime | None:
        if v is not None and v.tzinfo is None:
            return v.replace(tzinfo=UTC)
        return v


class EventBatchIn(BaseModel):
    events: list[EventIn]


class IngestError(BaseModel):
    index: int
    error: str


class IngestResult(BaseModel):
    accepted: int
    duplicates: int
    rejected: int
    errors: list[IngestError] = []


class BudgetIn(BaseModel):
    name: str = Field(..., min_length=1, max_length=120)
    scope_dimension: Dimension | None = None
    scope_value: str | None = Field(None, max_length=120)
    monthly_limit_usd: float = Field(..., gt=0)
    alert_threshold: float = Field(0.8, gt=0, le=1)
