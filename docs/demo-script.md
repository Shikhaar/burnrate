# 3-minute demo script

**Setup (before you present)**

```bash
make install && make seed
make dev                                                     # API :8000, dashboard :3000
cd backend && KEY=$(.venv/bin/burnrate create-key demo) && cd ..
```

---

**0:00 · The problem (20s)**
"Companies run AI across OpenAI, Anthropic and Google, plus coding agents like Claude Code. Every
vendor sends a separate bill, and none of them can tell you which *feature* or *customer* spent the money."

**0:20 · Overview (30s)** at `localhost:3000`
- Total spend, period-over-period change, projected month end.
- Stacked daily spend by provider. Point at the spike in the last three days.
- Coding-agents card: Claude Code spend per engineer, from Claude Code's own telemetry with zero code changes.

**0:50 · Anomalies & Forecast (30s)**
- "Burnrate caught that spike on its own: a retry loop in the support agent's ticket triage, +185% vs its baseline."
- Point out *Same incident: model…, project…*. The project, feature and model spikes are grouped as one incident, so you get one alert instead of three.
- Forecast: trend × weekday seasonality with a 90% band, shown against the company budget.

**1:20 · Insights (25s)**
- "It doesn't just report, it tells you what to fix, ranked by dollars." Two examples: cache the 13k-token RAG prompts, and move sentiment tagging off Opus.

**1:45 · Reconciliation (25s)**
- "Token math isn't the bill." The connectors pull real charges from the OpenAI and Anthropic cost APIs. Google runs +4.7% over the estimate, so it's flagged to investigate.

**2:10 · Live ingestion (30s)**
```bash
BURNRATE_API_KEY=$KEY python examples/live_traffic.py --rps 10
```
- Open **Requests**: new calls arrive priced, with cached tokens, latency and attribution.
- "It's one line with the SDK, or point any OpenTelemetry exporter at it. Nothing sits in your request path."

**2:40 · Engineering (20s)**
- Raw usage is immutable, cost is derived from a versioned price registry, so repricing never loses data.
- Ingestion is idempotent, API keys are hashed, migrations use Alembic, CI tests SQLite and Postgres, and both SDKs have zero dependencies.

---

**Likely questions**
- *Why not a gateway like LiteLLM or Portkey?* No migration and no added latency. Burnrate works alongside them and reconciles against the actual bill.
- *How accurate is the cost?* Exact for token pricing, versioned by date. Gaps from things like tool fees or rounding show up in reconciliation instead of being hidden.
- *Scale?* Rollups are indexed on `(day, dimension)`. The next step is mirroring events into ClickHouse behind the same service layer.
