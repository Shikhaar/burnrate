# Deploying Burnrate

Burnrate is three pieces: a stateless API container, a PostgreSQL database, and a static-ish Next.js app.

## Single host (Docker Compose)

```bash
git clone https://github.com/Shikhaar/burnrate && cd burnrate
docker compose up -d --build          # runs `alembic upgrade head` on API start
docker compose exec api burnrate create-key "prod-ingest"
docker compose exec api burnrate create-user you@company.com --name "Your Name"   # first dashboard login
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
| `BURNRATE_DASHBOARD_TOKEN` | Optional bearer token for scripts and automation on `/api/*`; works alongside user logins |
| `BURNRATE_SESSION_DAYS` | How long a dashboard sign-in lasts (default 14) |
| `BURNRATE_COOKIE_SECURE` | Default `true`: the session cookie is HTTPS-only. Set `false` only for plain-HTTP testing on a host other than localhost |
| `BURNRATE_ALERT_WEBHOOK_URL` | Slack-compatible incoming webhook. Optional: admins can connect Slack from Settings instead, which takes priority |
| `BURNRATE_SECRET_KEY` | Encrypts the Slack link and Jira token saved from Settings; required to save them. `burnrate generate-secret-key` makes one. See [Encrypted secrets](#encrypted-secrets) |
| `BURNRATE_ALERT_INTERVAL_MINUTES` | How often the API checks for new alerts (default 15; `0` = off, use `burnrate alerts` from cron) |
| `BURNRATE_LOG_FORMAT` / `BURNRATE_LOG_LEVEL` | `json` for log pipelines (default `text`); level defaults to `INFO` |
| `BURNRATE_INGEST_RATE_LIMIT_PER_MINUTE` | Requests per minute per ingest API key (default 1200; `0` disables) |
| `BURNRATE_API_RATE_LIMIT_PER_MINUTE` | Requests per minute per client IP on `/api/*` (default 600; `0` disables) |
| `BURNRATE_TRUSTED_PROXIES` | Comma-separated addresses of your own reverse proxies / load balancers. Only requests arriving from these have their `X-Forwarded-For` header used for the client address in the audit log; unset = the direct connection's address |
| `BURNRATE_REDIS_URL` | e.g. `redis://redis:6379/0`. Shares rate limits across API replicas; unset = per-process limits |
| `BURNRATE_OPENAI_ADMIN_KEY` / `BURNRATE_ANTHROPIC_ADMIN_KEY` | Billing sync for reconciliation. Full admin keys: see [Keys and access](#keys-and-access) |
| `BURNRATE_CURSOR_ADMIN_KEY` | Cursor team admin API key; `burnrate sync cursor` imports per-request usage. Full admin key |
| `BURNRATE_GITHUB_TOKEN` / `BURNRATE_GITHUB_ORG` | Token of an org owner or billing manager, and the org; `burnrate sync copilot` imports Copilot spend |

The admin keys are only needed by `burnrate sync`. Leave them off the API service; see below.

## Dashboard logins

The dashboard is open until the first user exists. From then on every page and every `/api/*`
call needs a signed-in session (or `BURNRATE_DASHBOARD_TOKEN`), including when every user is
disabled, which locks the dashboard rather than reopening it.

```bash
burnrate create-user ana@acme.io --name "Ana"     # prompts twice; or pipe it with --password-stdin
burnrate list-users                              # created, last sign-in, active/disabled
burnrate set-role ana@acme.io admin              # change a role; `superadmin` hands the one super admin role to them
burnrate reset-password ana@acme.io              # also signs them out everywhere
burnrate disable-user ana@acme.io                # blocks sign-in and ends their sessions
burnrate enable-user ana@acme.io
```

The first user you create is the super admin (later ones default to admin; pass `--role` to choose).
After that, admins manage everything from **Settings** in the dashboard:

- **Users:** add someone with a role and, optionally, a set of teams. Burnrate shows a one-time link
  (valid 7 days) for the admin to send; the person opens it and sets their own password. The same
  button gives a password-reset link later. Burnrate doesn't send email.
- **API keys:** create a key per app (shown once, with a copy button), see when each was last used,
  and revoke it. Revoked keys are tucked under a toggle and can then be deleted for good.
- **Alerts:** connect Slack (see [Alerts](#alerts)).

| Role | Can |
|---|---|
| Super admin (exactly one) | Everything an admin can, plus adding, changing, disabling and resetting the password of admins. Always sees every team |
| Admin (any number) | Members and viewers, API keys, alerts, Jira, budgets and the audit log. Always sees every team |
| Member | See spend, manage budgets, run an alert check |
| Viewer | See spend; change nothing |

A member or viewer can be limited to some teams. Every page, filter list and `/api/*` response is
then filtered on the server to usage events whose `team` is one of theirs (events with no team are
hidden). They can't see reconciliation (provider bills span every team) or the company-wide alert
history, and can only create budgets for their own teams. The CLI can set both:
`burnrate create-user pat@acme.io --role viewer --teams search,support`.

There is always exactly one super admin (the database enforces it), and only they can manage
admins, so an admin can't take over or lock out the people above them. No one can change their own
role. The super admin can't be demoted, disabled or given a second; instead they hand the role over:
in Settings, edit someone and pick **Super admin**, or run `burnrate set-role <email> superadmin` on
the server. The new super admin is promoted and the old one becomes an admin, in one step. When you
upgrade, the migration makes your earliest active all-teams admin the super admin.
`BURNRATE_DASHBOARD_TOKEN` and the CLI act as the super admin.

## Audit log

**Settings → Audit log** (admins only) lists the last 90 days of sign-ins, failed sign-ins, and
changes to people, API keys, budgets and alert connections, including changes made with the CLI on
the server. Each entry has who, when, from which address, and what changed (role viewer → member).
Secrets never appear: the log says only "changed" or "unchanged".

- **Can't be edited from Burnrate.** There's no way in the app or API to change or delete entries.
  Someone with direct database access still could, as with any table; if you need stronger
  guarantees, stream the table to write-once storage.
- **Failed sign-ins** are recorded with the email that was typed, as "Not signed in". To stop anyone
  flooding the log, each address can add at most 20 per 15 minutes; one further entry notes the rest
  weren't recorded.
- **Client addresses** come from the connection itself. Behind a proxy that address is the proxy's, so
  set `BURNRATE_TRUSTED_PROXIES` to your proxies' addresses. Burnrate then reads `X-Forwarded-For` from
  the right, skipping your proxies, so a client can't fake its address by sending the header itself.

## Alerts

Admins connect Slack under **Settings → Alerts**: paste an incoming-webhook link and Burnrate posts a
test message, saving the link only if it arrives. The page shows when alerts were last checked and
delivered, and the last delivery error if any. A link saved there takes priority over
`BURNRATE_ALERT_WEBHOOK_URL`.

The API checks budgets and spend spikes itself every `BURNRATE_ALERT_INTERVAL_MINUTES` (default 15).
Running several API replicas is fine: each condition alerts once, however many check it. Set the
interval to `0` to turn the timer off and run `burnrate alerts` from cron instead.

**Jira.** Under the same page, admins can also have alerts filed as Jira tickets: enter the Jira
address, an account email and [API token](https://id.atlassian.com/manage-profile/security/api-tokens),
the project key and the ticket type. Burnrate checks the login, project and ticket type before
saving, without creating anything; **Create test ticket** files one on purpose. By default only
serious alerts (budget exceeded, high-severity spikes) become tickets; you can include warnings too.
Each alert files at most one ticket, even with several API replicas, and failed attempts are retried
on later checks (up to 5). Tickets are labelled `burnrate` and `burnrate-budget` or `burnrate-anomaly`.

The token acts as the Jira user who created it, so create a dedicated bot user that can only create
issues in that project.

### Encrypted secrets

The Slack link and Jira token are the only secrets Burnrate stores in its database, and they're
encrypted (Fernet: AES with an integrity check) with `BURNRATE_SECRET_KEY`, which lives only in the
server's environment. Someone with a copy of the database or a backup, but not the key, can't read
or use them. They are never sent back to the browser either: Settings shows only a shortened link
and the token's last four characters.

```bash
burnrate generate-secret-key          # prints a new key
```

- **Required to save them.** Without the key, connecting Slack or Jira is refused rather than stored
  unprotected, and Settings says how to set it.
- **Upgrading:** values saved by an older version, in plain text, are encrypted the next time the
  API starts with a key set.
- **Rotating:** set `BURNRATE_SECRET_KEY=<new>,<old>` and restart. The first key encrypts; all listed
  keys can decrypt, and saved values move to the new key on start. Then remove the old key.
- **Lost or wrong key:** saved secrets can't be read. Burnrate keeps running, treats Slack and Jira as
  disconnected, says so in Settings, and never overwrites the stored values. Reconnect them, or
  restore the right key.

Keep the key in a secrets manager, never in the database, its backups or the repository.

Passwords need at least 10 characters and are stored as salted scrypt hashes. Sessions are random
tokens in an HttpOnly, SameSite=Lax cookie; only their hash is stored. Writes made with the cookie
must carry `X-Requested-With: burnrate`, which blocks cross-site request forgery. After 10 failed
sign-ins for one email from one address, that pair is locked out for 15 minutes (counted per API
replica).

The session cookie belongs to the API's host, so serve the dashboard and API from the **same site**,
e.g. `burnrate.acme.com` and `api.burnrate.acme.com`, over HTTPS, and list the dashboard origin in
`BURNRATE_CORS_ORIGINS`.

## Keys and access

Burnrate never needs your code, your repositories or the API keys your apps use for inference.
It handles two kinds of credentials, with very different risk:

**Ingest keys (`br_live_…`)** live in your apps. They can only write usage events: they can't read
spend, call `/api/*` or reach any provider. Burnrate stores only their SHA-256 hash. A leaked one
lets someone add fake usage, nothing more. Create one per service, and manage them with:

```bash
burnrate list-keys          # id, name, created, last used (to within 5 minutes), active/revoked
burnrate revoke-key 3       # by id, or by the full key, e.g. one found in a leak
```

A revoked key is rejected from its next request. To rotate, create the new key, deploy it, wait for
the old key's "last used" to stop moving, then revoke it.

**Provider admin keys** are powerful. Burnrate only calls read-only endpoints with them (cost and
usage reports), but the keys themselves are not read-only:

| Key | What Burnrate calls | What the key can also do |
|---|---|---|
| OpenAI admin key | Costs API | Manage the organization: invite and remove members, projects, API keys |
| Anthropic admin key | Cost report | Manage the organization: members, workspaces, API keys |
| Cursor admin API key | Usage events | Manage team settings such as members' spend limits |
| GitHub token | Billing usage report | Only what you grant: use a fine-grained token with the least org access the billing usage endpoint accepts (GitHub's docs for that endpoint list it) |

Treat the first three like root credentials:

- **Keep them off the API service.** The web-facing API never uses them. Run `burnrate sync` as its
  own scheduled job (same image, different command) and give only that job the keys.
- **Inject them from a secrets manager** (AWS Secrets Manager, Vault, Kubernetes secrets, your
  platform's secret store) as environment variables. Burnrate never writes them to its database.
- **Skip them if you don't need them.** Without them you lose billing reconciliation and the
  Cursor/Copilot import; SDK, OpenTelemetry and Claude Code ingestion are unaffected.
- **Rotate them on the provider's side** if one may have leaked; revoking there takes effect at once.

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

Alerts need no scheduler: the API checks on its own (see [Alerts](#alerts)). Run the billing and
coding-tool sync from cron, a platform scheduler, or a Kubernetes CronJob using the API image:

```
0 6 * * *     burnrate sync all --days 7   # billed costs + Cursor/Copilot usage (configured connectors only)
```

Give the admin keys only to the `burnrate sync` job, not to the API service (see [Keys and access](#keys-and-access)).

## Price updates

Edit or extend a registry YAML (new rows with a new `effective_from`, never edits), then:

```bash
burnrate pricing my-prices.yaml --since 2026-10-01   # adds entries and reprices affected events
```
