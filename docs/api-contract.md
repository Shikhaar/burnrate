# Burnrate API contract (dashboard ↔ backend)

Base URL: `NEXT_PUBLIC_API_URL` (default `http://localhost:8000`). All money is USD floats, all dates ISO-8601 (`YYYY-MM-DD` for days, full timestamps for events).
Common query params: `days` (window length, default 30). Dimension values: `provider | model | project | feature | team | environment | customer | user`.

Auth: once any user exists, `/api/*` needs the `burnrate_session` cookie (send with `credentials: "include"`) or `Authorization: Bearer $BURNRATE_DASHBOARD_TOKEN`; otherwise `401`. Cookie-authenticated writes also need `X-Requested-With: burnrate` (else `403`).

| Method | Path | Response |
|---|---|---|
| POST | `/api/auth/login` | body `{email, password}` → `{user:{id,email,name}}` and sets the session cookie; `401` wrong credentials, `429` too many failures |
| POST | `/api/auth/logout` | `204`, clears the session |
| GET | `/api/auth/me` | `{auth_required, user:{id,email,name,role,teams}|null}` (never `401`) |
| GET | `/api/auth/invite?token=` | `{email,name}`, or `404` if the link expired or was used |
| POST | `/api/auth/invite` | body `{token, password}` → sets the password, signs in (cookie), `{user}` |
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
| GET | `/api/alerts?days=30` | `{items:[{id,kind:"budget"|"anomaly",severity,title,detail,created_at,delivered,delivery_error,jira_issue_key,jira_url}]}` |
| POST | `/api/alerts/evaluate` | `{fired}` — check now (the API also checks every 15 minutes on its own) |
| GET | `/api/pricing` | `{items:[{provider,model,input_per_mtok,output_per_mtok,cached_input_per_mtok,cache_write_per_mtok,effective_from}]}` |

Ingestion (used by SDKs, not the dashboard): OTLP/HTTP JSON at `POST /v1/otlp/v1/traces` (GenAI spans) and `POST /v1/otlp/v1/metrics` (Claude Code counters). `POST /v1/events` with header `X-API-Key`, body `{events:[...]}` → `{accepted, rejected, errors[]}`.

## Roles

`viewer` may only `GET`; writes return `403`. Users limited to teams get every response filtered to
their teams, `403` on `/api/reconciliation` and `/api/alerts*`, and may only create or delete budgets
scoped to `team=<one of theirs>`.

`superadmin` and `admin` both use the admin endpoints below, but only a `superadmin` may create,
change, disable or issue an invite/reset link for a user whose role is (or would become) `admin` or
`superadmin`; anyone else gets `403`. The dashboard token acts as a `superadmin`.

## Admin only (`403` for members and viewers)

| Method | Path | Response |
|---|---|---|
| GET | `/api/settings/alerts` | `{connected, source:"dashboard"|"environment"|null, webhook_hint, is_slack, timer:{enabled,interval_minutes,last_run_at,last_run_error}, last_delivered_at, last_failure:{at,error,title}|null, undelivered}` |
| PUT | `/api/settings/alerts` | body `{webhook_url}` (https). Posts a test message first; `400` with the reason if it doesn't arrive, else saves and returns the status |
| DELETE | `/api/settings/alerts` | removes the dashboard webhook; returns the status |
| POST | `/api/settings/alerts/test` | `{sent:true}`, `400` with the reason, or `409` if nothing is connected |
| GET | `/api/settings/jira` | `{connected, site, email, token_hint, project_key, issue_type, min_severity:"high"|"medium", last_ticket:{key,url,title}|null, last_failure:{error,title}|null}` |
| PUT | `/api/settings/jira` | body `{site, email, api_token?, project_key, issue_type, min_severity}`; omit `api_token` to keep the saved one. Checks login, project and type first (`400` with the reason), then saves → status + `verified:{account,project_name,issue_types}` |
| DELETE | `/api/settings/jira` | disconnects; returns the status |
| POST | `/api/settings/jira/test` | creates one test ticket → `{key, url}` |
| GET | `/api/users` | `{items:[{id,email,name,role,teams,created_at,last_login_at,disabled,invite_pending}]}` |
| POST | `/api/users` | body `{email,name?,role,teams|null}` → `{user, invite_path, invite_expires_at}` |
| PATCH | `/api/users/{id}` | body any of `{name,role,teams,disabled}` → `{user}`; `409` for changes that would remove the last super admin or admin, or change your own role or disable you |
| POST | `/api/users/{id}/invite` | new one-time link (invite or password reset) → `{user, invite_path, invite_expires_at}` |
| GET | `/api/keys` | `{items:[{id,name,prefix,created_at,last_used_at,revoked_at}]}` |
| POST | `/api/keys` | body `{name}` → `{key, secret}`; `secret` is the full key, returned only here |
| POST | `/api/keys/{id}/revoke` | `{key}` |
| DELETE | `/api/keys/{id}` | `204`; only revoked keys (`409` for an active one: revoke it first) |
| GET | `/api/audit-logs?limit=50&offset=0&action=&actor=&days=30` | `{total, items:[{id,timestamp,actor_type:"user"|"cli"|"token"|"anonymous"|"system",actor_id,actor_email,action,target_type,target_id,details,ip_address}]}`. `action` and `actor` match substrings. Read-only: there's no way to edit or delete entries |
