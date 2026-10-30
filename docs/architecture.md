# Architecture

```
  Your apps ──► Burnrate SDK (batched, async) ──┐
  OTel GenAI spans ──► OTLP/HTTP receiver ──────┼──► POST /v1/*  ──► usage_events (immutable)
                                                │                        │
                                                │                 pricing engine (versioned registry)
                                                │                        ▼
  Provider admin APIs ──► billing connectors ───┴──► billed_costs    event_costs (derived)
                                                                         │
                     analytics · forecast · anomalies · budgets · insights · reconciliation
                                                                         │
                                                         /api/* ──► Next.js dashboard
```

## Key decisions

**Raw usage and cost are separate tables.** `usage_events` records what was observed;
`event_costs` records what we *think* it cost and which price entry produced the number. Prices
change, registries get corrected, and providers introduce new token classes. Repricing rewrites
only derived data (`burnrate pricing --since 2026-09-01`) and never loses the original observation.

**Prices are versioned, not edited.** A price change is a new `price_entries` row with an
`effective_from` date, so a September report stays reproducible after an October price cut.

**Estimated ≠ billed, and we show the gap.** Token-based estimates miss things such as
unlogged retries, tool and search fees, rounding, and billing-day boundaries. The billing
connectors pull what each provider actually charged into `billed_costs`. The reconciliation view
compares the two only over days the provider has billed, and flags drift above a tolerance (2%).

**Two collection paths, no forced gateway.** Teams add the SDK (one line per call) or point an
existing OpenTelemetry pipeline at `/v1/otlp/v1/traces`. Nothing sits in the inference request
path, so a Burnrate outage can never take down the product. The SDK queues in memory, flushes on
a background thread with retry/backoff, and swallows its own errors.

**Bounded, traceable API.** Ingestion is rate-limited per API key, so one runaway integration can't starve
the rest, and the dashboard API is limited per client IP. Every request gets an `X-Request-ID` that appears on
every log line written while handling it, and logs can be emitted as JSON for any log pipeline.

**Idempotent ingestion.** `request_id` carries a unique constraint, and SDK retries or replayed OTel
batches are counted as duplicates, so they are never double-billed.

**Statistics that don't cry wolf.** Anomaly detection uses a robust z-score (median/MAD over a
trailing 14-day window). A day must be statistically unusual (z ≥ 3.5), materially larger
(+50%), *and* at least $5 above baseline. One earlier spike can't inflate the baseline and mask
the next one.

**Forecasting that's explainable.** The model is a linear trend over 28 days multiplied by a
day-of-week seasonality factor, with a 90% band from residual variance. It's simple enough for a
finance reviewer to verify, and the API returns which method was used.

**Recommendations are ranked by dollars.** Each insight rule (frontier model on light traffic,
uncached long prompts, paid-for failures) estimates monthly savings from observed traffic.

## Storage

SQLAlchemy 2.0 with one schema for SQLite (local/demo) and PostgreSQL (production; CI runs both).
Hot query paths are indexed on `(day, provider)`, `(day, project)` and `timestamp`. A `day` column is
denormalized at ingest, so daily rollups never need date functions that differ between databases.
At very high volume, the next step is to mirror `usage_events ⨝ event_costs` into a columnar store
(ClickHouse) behind the same service functions.
