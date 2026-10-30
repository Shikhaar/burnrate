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
| `BURNRATE_OPENAI_ADMIN_KEY` / `BURNRATE_ANTHROPIC_ADMIN_KEY` | Read-only billing sync for reconciliation |

## Scheduled jobs

Run these from cron, a platform scheduler, or a Kubernetes CronJob using the API image:

```
*/15 * * * *  burnrate alerts          # budget + anomaly notifications (deduplicated)
0 6 * * *     burnrate sync all --days 7   # pull provider-billed costs
```

## Price updates

Edit or extend a registry YAML (new rows with a new `effective_from`, never edits), then:

```bash
burnrate pricing my-prices.yaml --since 2026-10-01   # adds entries and reprices affected events
```
