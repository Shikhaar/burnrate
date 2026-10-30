<div align="center">

# 🔥 Burnrate

**See where your AI money goes.**

Burnrate tracks LLM token usage and spend across OpenAI, Anthropic, Google and others. It attributes every dollar to the project, feature, team and customer that spent it, forecasts month-end spend, catches cost spikes, and reconciles its estimates against what providers actually bill.

[Quickstart](#quickstart) · [Features](#features) · [Architecture](docs/architecture.md) · [Event schema](docs/event-schema.md) · [API](docs/api-contract.md)

</div>

![Burnrate dashboard](docs/screenshots/overview.png)

---

## Why

Provider dashboards show one bill per vendor. Engineering and finance need different answers:

- *Which feature doubled our Anthropic bill this week?*
- *What does customer Acme cost us in AI each month?*
- *Will we blow the budget before month end?*
- *Does our usage data actually match the invoice?*
- *Where can we cut spend without hurting quality?*

Burnrate answers these from one place. It doesn't sit in the request path, so no proxy or gateway migration is needed.

## Features

| | |
|---|---|
| **Unified spend** | Every provider and model in one ledger, with daily trends and period-over-period change. |
| **Attribution** | Slice by provider, model, project, feature, team, environment or customer. |
| **Request log** | Per-call tokens (input / cached / cache-write / output / reasoning), cost, latency and status. |
| **Versioned pricing engine** | Price registry with `effective_from` versions, snapshot-name resolution (`gpt-4o-2024-08-06` → `gpt-4o`), and cache-read/cache-write rates. Repricing rewrites only derived data. |
| **Billing reconciliation** | Pulls real charges from the OpenAI Costs API and Anthropic Cost Report. Flags providers whose bill drifts from the token-based estimate. |
| **Forecasting** | Month-end projection from a 28-day trend × weekday seasonality, with a 90% confidence band. |
| **Anomaly detection** | Robust z-score (median/MAD) per project, feature and model, with severity and plain-English explanations. |
| **Budgets** | Monthly limits company-wide or per project, team, feature or customer, with projected-overrun warnings. |
| **Coding-agent spend** | Point Claude Code's built-in OpenTelemetry export at Burnrate to see coding-agent spend per engineer, model and session next to production spend. |
| **Alerts** | Budget and anomaly alerts sent to Slack or any webhook. Deduplicated, so each condition notifies once (`burnrate alerts` from cron). |
| **Savings insights** | Ranked by estimated $/month: frontier models on light traffic, uncached long prompts, money spent on failed calls. |
| **Two ingestion paths** | Zero-dependency Python and TypeScript SDKs (background batching, retries, context-based attribution) **or** OTLP/HTTP using OpenTelemetry GenAI semantic conventions. |
| **Production basics** | Hashed API keys, idempotent ingestion, optional dashboard token, health checks, Docker, CI on SQLite + Postgres. |

## Screenshots

| Anomalies & forecast | Savings insights |
|---|---|
| ![Forecast](docs/screenshots/forecast.png) | ![Insights](docs/screenshots/insights.png) |
| **Billing reconciliation** | **Explore by feature** |
| ![Reconciliation](docs/screenshots/reconciliation.png) | ![Explore](docs/screenshots/explore.png) |

## Quickstart

### Docker (Postgres + API + dashboard)

```bash
docker compose up -d --build
docker compose run --rm seed        # 90 days of realistic demo data
open http://localhost:3000          # dashboard;  API docs at http://localhost:8000/docs
```

### Local dev

```bash
make install    # backend venv + web deps
make seed       # demo dataset into SQLite
make dev        # API on :8000, dashboard on :3000
make test       # backend + SDK tests
```

## Send real usage

```bash
burnrate create-key "support-agent prod"     # → br_live_…  (only the hash is stored)
```

**Python SDK**

```python
from anthropic import Anthropic
from burnrate_sdk import Burnrate

br = Burnrate(api_key="br_live_…", endpoint="https://burnrate.internal", project="support-agent")
claude = Anthropic()

with br.attribute(feature="ticket-triage", customer_id="acme"):
    msg = claude.messages.create(model="claude-sonnet-4-5", max_tokens=512, messages=[...])
    br.track_anthropic(msg)          # also: track_openai(resp), track_gemini(resp, model=...)
```

The SDK only sends token counts and metadata, never prompts or completions. If Burnrate is unreachable, your app keeps running and the SDK logs a warning.

**TypeScript SDK**

```ts
import { Burnrate } from "@burnrate/sdk";
const br = new Burnrate({ apiKey: process.env.BURNRATE_API_KEY, project: "rag-search" });
const res = await openai.chat.completions.create({ model: "gpt-4.1", messages });
br.trackOpenAI(res, { feature: "answer-synthesis", customer_id: "acme" });
```

**OpenTelemetry:** point any OTLP/HTTP JSON exporter at `POST /v1/otlp/v1/traces` with the `X-API-Key` header. Spans carrying `gen_ai.*` attributes become usage events. Add `burnrate.feature`, `burnrate.team` and `burnrate.customer_id` for attribution.

**Claude Code (coding-agent spend per engineer)** — no code changes, just environment variables (or managed settings):

```bash
export CLAUDE_CODE_ENABLE_TELEMETRY=1
export OTEL_METRICS_EXPORTER=otlp
export OTEL_EXPORTER_OTLP_PROTOCOL=http/json
export OTEL_EXPORTER_OTLP_ENDPOINT=https://burnrate.internal/v1/otlp      # exporter appends /v1/metrics
export OTEL_EXPORTER_OTLP_HEADERS="X-API-Key=br_live_…"
export OTEL_RESOURCE_ATTRIBUTES="burnrate.team=platform"                   # optional attribution
```

The `claude_code.token.usage` and `claude_code.cost.usage` counters become usage events under project `claude-code`, attributed by `user.email`, model and session. Burnrate prices them from its registry. For models it doesn't have a price for yet, it falls back to the cost Claude Code reports.

**Raw HTTP**

```bash
curl -X POST localhost:8000/v1/events -H "X-API-Key: $KEY" -H 'content-type: application/json' -d '{
  "events": [{"provider":"openai","model":"gpt-4o","project":"search","feature":"answer",
              "input_tokens":1200,"cached_input_tokens":800,"output_tokens":350,"request_id":"req_123"}]}'
```

**Reconcile against real bills**

```bash
export BURNRATE_OPENAI_ADMIN_KEY=sk-admin-…  BURNRATE_ANTHROPIC_ADMIN_KEY=sk-ant-admin-…
burnrate sync all --days 31
```

## Demo dataset

`burnrate seed` generates a fictional SaaS company with 14 AI workloads across 6 products (including Claude Code), 6 teams and 10 customers (about 260k calls over 90 days). It contains planted problems for the dashboard to find:

- a **retry loop** in the support agent's ticket triage (last 3 days, about 3×) → high-severity anomaly, exceeded budget
- a **bulk content job** run on a frontier model → past anomaly
- **Opus used for sentiment tagging** (900-token prompts) → model-downgrade insight
- **Claude Code** usage across 8 engineers, with one power user → per-engineer breakdown
- **12k-token RAG prompts with no caching** → prompt-caching insight
- a **Google bill running 4.7% above estimate** → reconciliation flag

## Tech

**Backend:** Python 3.12, FastAPI, SQLAlchemy 2.0, Pydantic v2, PostgreSQL / SQLite, httpx · **Dashboard:** Next.js 15, TypeScript, Tailwind CSS v4, Recharts · **Infra:** Docker Compose, GitHub Actions (backend on SQLite + Postgres matrix, SDKs on Python 3.10 & 3.13 and Node 22, web lint + build)

```
backend/     FastAPI service, pricing engine, analytics, connectors, CLI, tests
web/         Next.js dashboard
sdk/python/      zero-dependency Python SDK
sdk/typescript/  zero-dependency TypeScript SDK
docs/        architecture, event schema, API contract
```

## Roadmap

- LangChain / LlamaIndex / Vercel AI SDK callbacks
- More coding agents (Cursor, Copilot, Codex) via their admin APIs
- Email alerts and per-team routing
- Multi-tenant orgs with SSO and role-based access control
- ClickHouse rollups for very high event volumes

## License

MIT
