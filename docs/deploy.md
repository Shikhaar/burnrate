# Deploying Burnrate

Burnrate is three pieces: a stateless API container, a PostgreSQL database, and a static-ish Next.js app.

## Single host (Docker Compose)

```bash
git clone https://github.com/Shikhaar/burnrate && cd burnrate
docker compose up -d --build          # runs `alembic upgrade head` on API start
docker compose exec api burnrate create-key "prod-ingest"
```

Put a TLS-terminating proxy (Caddy, nginx, a cloud load balancer) in front of `:8000` and `:3000`.

## Managed platforms (Render / Railway / Fly.io)

| Component | Setting |
|---|---|
| Database | Managed Postgres 14+ |
| API | Docker, `backend/Dockerfile`, port 8000, health check `/healthz` |
| Web | Docker, `web/Dockerfile`, build arg `NEXT_PUBLIC_API_URL=https://api.your-domain` |

The API container runs migrations automatically on boot. Scale it horizontally as needed: all
state lives in Postgres.

## Configuration

| Variable | Purpose |
|---|---|
| `BURNRATE_DATABASE_URL` | `postgresql+psycopg://user:pass@host:5432/burnrate` |
| `BURNRATE_CORS_ORIGINS` | JSON list of dashboard origins, e.g. `["https://burnrate.your-domain"]` |
| `BURNRATE_DASHBOARD_TOKEN` | Bearer token required on `/api/*` (strongly recommended outside localhost) |
| `BURNRATE_ALERT_WEBHOOK_URL` | Slack-compatible incoming webhook |
| `BURNRATE_LOG_FORMAT` / `BURNRATE_LOG_LEVEL` | `json` for log pipelines (default `text`); level defaults to `INFO` |
| `BURNRATE_INGEST_RATE_LIMIT_PER_MINUTE` | Requests per minute per ingest API key (default 1200; `0` disables) |
| `BURNRATE_API_RATE_LIMIT_PER_MINUTE` | Requests per minute per client IP on `/api/*` (default 600; `0` disables) |
| `BURNRATE_REDIS_URL` | e.g. `redis://redis:6379/0`. Shares rate limits across API replicas; unset = per-process limits |
| `BURNRATE_OPENAI_ADMIN_KEY` / `BURNRATE_ANTHROPIC_ADMIN_KEY` | Read-only billing sync for reconciliation |

## Logging and rate limits

Every response carries an `X-Request-ID` (an incoming one is reused, so you can trace a call from your
load balancer through Burnrate). With `BURNRATE_LOG_FORMAT=json`, each request is logged as one line:

```json
{"ts": "2026-10-30T14:46:22.619+00:00", "level": "info", "logger": "burnrate.http", "msg": "request",
 "method": "GET", "path": "/api/pricing", "status": 200, "duration_ms": 2.6, "client": "10.0.0.7",
 "request_id": "b29b9429…"}
```

Over the limit, the API returns `429` with `Retry-After`; both SDKs back off and retry. With
`BURNRATE_REDIS_URL` set, every replica draws from one shared bucket per caller (the Compose stack does
this). Without it, limits are per process, so N replicas allow N× the setting. If Redis becomes
unreachable, requests are allowed through and a warning is logged. Behind a load balancer, set
`FORWARDED_ALLOW_IPS` to its address (the image starts uvicorn with `--proxy-headers`) so per-IP limits and
logs see the real client IP rather than the proxy's.

## Metrics

`GET /metrics` serves Prometheus metrics (it requires the dashboard token when one is set):

| Metric | Labels |
|---|---|
| `burnrate_http_requests_total` / `burnrate_http_request_duration_seconds` | `method`, `route` (template, e.g. `/api/breakdown`), `status` |
| `burnrate_events_ingested_total` | `outcome`: `accepted` · `duplicate` · `rejected` |
| `burnrate_rate_limited_total` | `scope`: `ingest` · `api` |
| `burnrate_alerts_fired_total` | `kind`, `severity` |
| `burnrate_webhook_deliveries_total` | `outcome`: `delivered` · `failed` |

## Scheduled jobs

Run these from cron, a platform scheduler, or a Kubernetes CronJob using the API image:

```
*/15 * * * *  burnrate alerts          # budget + anomaly notifications (deduplicated; failed webhooks retried)
0 6 * * *     burnrate sync all --days 7   # pull provider-billed costs
```

## Price updates

Edit or extend a registry YAML (new rows with a new `effective_from`, never edits), then:

```bash
burnrate pricing my-prices.yaml --since 2026-10-01   # adds entries and reprices affected events
```
