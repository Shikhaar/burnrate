# Burnrate API contract (dashboard ↔ backend)

Base URL: `NEXT_PUBLIC_API_URL` (default `http://localhost:8000`). All money is USD floats, all dates ISO-8601 (`YYYY-MM-DD` for days, full timestamps for events).
Common query params: `days` (window length, default 30). Dimension values: `provider | model | project | feature | team | environment | customer | user`.

| Method | Path | Response |
|---|---|---|
| GET | `/api/meta` | `{providers[], models[], projects[], features[], teams[], environments[]}` |
| GET | `/api/overview?days=30` | `{period:{start,end}, total_cost_usd, total_tokens, request_count, prev_period_cost_usd, month_to_date_cost_usd, projected_month_cost_usd, unpriced:[{provider,model,requests}], by_provider:[{key,cost_usd,tokens,requests}], daily:[{date,cost_usd,tokens,requests}]}` |
| GET | `/api/breakdown?dimension=model&days=30&limit=10` | `{dimension, items:[{key,cost_usd,tokens,requests,share,change_pct}]}` — `share` 0..1, `change_pct` vs previous equal window (null if no prior data) |
| GET | `/api/timeseries?group_by=provider&days=30` | `{group_by, series_keys:[...], points:[{date, values:{<key>: cost_usd}}]}` |
| GET | `/api/events?limit=50&offset=0&provider=&model=&project=` | `{total, items:[{id,timestamp,provider,model,project,feature,team,environment,customer_id,input_tokens,output_tokens,cached_input_tokens,reasoning_tokens,cost_usd,pricing:"priced"|"provider_reported"|"unpriced",latency_ms,status}]}` |
| GET | `/api/anomalies?days=30` | `{items:[{id,date,dimension,key,cost_usd,baseline_usd,z_score,change_pct,severity:"low"|"medium"|"high",message,related:["<dimension>:<key>"]}]}` — same-day spikes caused by the same money are grouped into one incident under the most specific dimension |
| GET | `/api/forecast` | `{month, month_to_date_usd, projected_usd, low_usd, high_usd, daily:[{date, actual_usd|null, forecast_usd|null}]}` |
| GET | `/api/reconciliation?days=30` | `{tolerance_pct, items:[{provider,estimated_usd,billed_usd,delta_usd,delta_pct,status:"ok"|"investigate"|"no_billing_data"}], daily:[{date,provider,estimated_usd,billed_usd|null}]}` — numeric item fields are null when status is `no_billing_data`; totals only cover days the provider has billed |
| GET | `/api/budgets` | `{items:[{id,name,scope_dimension|null,scope_value|null,monthly_limit_usd,alert_threshold,spent_usd,projected_usd,utilization,status:"ok"|"warning"|"exceeded"}]}` |
| POST | `/api/budgets` | body `{name, scope_dimension|null, scope_value|null, monthly_limit_usd, alert_threshold}` → budget object |
| DELETE | `/api/budgets/{id}` | 204 |
| GET | `/api/insights` | `{items:[{id,kind,title,detail,estimated_monthly_savings_usd,scope}]}` |
| GET | `/api/alerts?days=30` | `{items:[{id,kind:"budget"|"anomaly",severity,title,detail,created_at,delivered,delivery_error}]}` |
| POST | `/api/alerts/evaluate` | `{fired}` — evaluate now (normally run from cron via `burnrate alerts`) |
| GET | `/api/pricing` | `{items:[{provider,model,input_per_mtok,output_per_mtok,cached_input_per_mtok,cache_write_per_mtok,effective_from}]}` |

Ingestion (used by SDKs, not the dashboard): OTLP/HTTP JSON at `POST /v1/otlp/v1/traces` (GenAI spans) and `POST /v1/otlp/v1/metrics` (Claude Code counters). `POST /v1/events` with header `X-API-Key`, body `{events:[...]}` → `{accepted, rejected, errors[]}`.
