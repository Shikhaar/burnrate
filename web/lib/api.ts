/**
 * Typed client for the Burnrate API.
 * Source of truth: docs/api-contract.md
 */
import useSWR, { type SWRConfiguration } from "swr";

export const API_URL = (process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000").replace(/\/+$/, "");

/* ------------------------------------------------------------------ */
/* Types                                                               */
/* ------------------------------------------------------------------ */

export const DIMENSIONS = ["provider", "model", "project", "feature", "team", "environment", "customer", "user"] as const;
export type Dimension = (typeof DIMENSIONS)[number];

export interface Meta {
  providers: string[];
  models: string[];
  projects: string[];
  features: string[];
  teams: string[];
  environments: string[];
}

export interface Period {
  start: string;
  end: string;
}

export interface ProviderTotal {
  key: string;
  cost_usd: number;
  tokens: number;
  requests: number;
}

export interface DailyTotal {
  date: string;
  cost_usd: number;
  tokens: number;
  requests: number;
}

export interface Overview {
  period: Period;
  total_cost_usd: number;
  total_tokens: number;
  request_count: number;
  prev_period_cost_usd: number;
  month_to_date_cost_usd: number;
  projected_month_cost_usd: number;
  /** Requests counted at $0 because their model has no registry price. */
  unpriced?: { provider: string; model: string; requests: number }[];
  by_provider: ProviderTotal[];
  daily: DailyTotal[];
}

export interface BreakdownItem {
  key: string;
  cost_usd: number;
  tokens: number;
  requests: number;
  /** 0..1 */
  share: number;
  /** vs previous equal window; null when there is no prior data */
  change_pct: number | null;
}

export interface Breakdown {
  dimension: Dimension;
  items: BreakdownItem[];
}

export interface TimeseriesPoint {
  date: string;
  values: Record<string, number>;
}

export interface Timeseries {
  group_by: Dimension;
  series_keys: string[];
  points: TimeseriesPoint[];
}

export type EventStatus = string; // "success" | "error" | ... (open-ended)

export interface UsageEvent {
  id: string | number;
  request_id?: string | null;
  timestamp: string;
  provider: string;
  model: string;
  project: string | null;
  feature: string | null;
  team: string | null;
  environment: string | null;
  customer_id: string | null;
  input_tokens: number;
  output_tokens: number;
  cached_input_tokens: number;
  reasoning_tokens: number;
  cost_usd: number;
  latency_ms: number | null;
  status: EventStatus;
  /** How cost_usd was derived. */
  pricing?: "priced" | "provider_reported" | "unpriced";
}

export interface EventsPage {
  total: number;
  items: UsageEvent[];
}

export type Severity = "low" | "medium" | "high";

export interface Anomaly {
  id: string | number;
  date: string;
  dimension: Dimension | string;
  key: string;
  cost_usd: number;
  baseline_usd: number;
  z_score: number;
  change_pct: number | null;
  severity: Severity;
  message: string;
  /** Other dimensions showing the same excess spend ("model:claude-sonnet-4-5"). */
  related?: string[];
}

export interface AnomaliesResponse {
  items: Anomaly[];
}

export interface ForecastDay {
  date: string;
  actual_usd: number | null;
  forecast_usd: number | null;
}

export interface Forecast {
  month: string;
  /** human-readable description of the forecasting model */
  method?: string;
  month_to_date_usd: number;
  projected_usd: number;
  low_usd: number;
  high_usd: number;
  daily: ForecastDay[];
}

export type ReconciliationStatus = "ok" | "investigate" | "no_billing_data";

export interface ReconciliationItem {
  provider: string;
  /** numeric fields are null when status is "no_billing_data" */
  estimated_usd: number | null;
  billed_usd: number | null;
  delta_usd: number | null;
  delta_pct: number | null;
  status: ReconciliationStatus;
}

export interface ReconciliationDaily {
  date: string;
  provider: string;
  estimated_usd: number;
  billed_usd: number | null;
}

export interface Reconciliation {
  /** |delta_pct| above this is flagged "investigate" (percent points) */
  tolerance_pct: number;
  items: ReconciliationItem[];
  daily: ReconciliationDaily[];
}

export type BudgetStatus = "ok" | "warning" | "exceeded";

export interface Budget {
  id: string | number;
  name: string;
  scope_dimension: Dimension | null;
  scope_value: string | null;
  monthly_limit_usd: number;
  /** 0..1 fraction of the limit that triggers a warning */
  alert_threshold: number;
  spent_usd: number;
  projected_usd: number;
  /** spent / limit, 0..n */
  utilization: number;
  status: BudgetStatus;
}

export interface BudgetsResponse {
  items: Budget[];
}

export interface BudgetCreate {
  name: string;
  scope_dimension: Dimension | null;
  scope_value: string | null;
  monthly_limit_usd: number;
  alert_threshold: number;
}

export interface Insight {
  id: string | number;
  kind: string;
  title: string;
  detail: string;
  estimated_monthly_savings_usd: number;
  /** Contract leaves the shape open: usually a string like "project:search" or an object. */
  scope: string | Record<string, unknown> | null;
}

export interface InsightsResponse {
  items: Insight[];
}

export interface PriceEntry {
  provider: string;
  model: string;
  input_per_mtok: number;
  output_per_mtok: number;
  cached_input_per_mtok: number | null;
  cache_write_per_mtok: number | null;
  effective_from: string | null;
}

export interface Alert {
  id: string | number;
  kind: "budget" | "anomaly" | string;
  severity: string;
  title: string;
  detail: string;
  created_at: string;
  delivered: boolean;
  delivery_error: string | null;
  jira_issue_key?: string | null;
  jira_url?: string | null;
}

export interface AlertsResponse {
  items: Alert[];
}

export interface PricingResponse {
  items: PriceEntry[];
}

/* ------------------------------------------------------------------ */
/* Transport                                                           */
/* ------------------------------------------------------------------ */

export class ApiError extends Error {
  /** HTTP status, or 0 when the server could not be reached at all */
  status: number;
  url: string;
  constructor(message: string, status: number, url: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.url = url;
  }
  get unreachable() {
    return this.status === 0;
  }
}

type QueryValue = string | number | boolean | null | undefined;

export function buildPath(path: string, query?: Record<string, QueryValue>): string {
  if (!query) return path;
  const params = new URLSearchParams();
  for (const [k, v] of Object.entries(query)) {
    if (v === undefined || v === null || v === "") continue;
    params.set(k, String(v));
  }
  const qs = params.toString();
  return qs ? `${path}?${qs}` : path;
}

/** Signed out (or session expired): go to the login page and come back here afterwards. */
export function redirectToLogin() {
  if (typeof window === "undefined" || window.location.pathname === "/login") return;
  const next = window.location.pathname + window.location.search;
  // Plain navigation: this runs outside React, and a reload also drops responses cached for the old session.
  // eslint-disable-next-line @next/next/no-location-assign-relative-destination
  window.location.assign(`/login?next=${encodeURIComponent(next)}`);
}

export async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const url = `${API_URL}${path}`;
  let res: Response;
  try {
    res = await fetch(url, {
      ...init,
      // Send the session cookie; the header lets the API reject cross-site forged writes.
      credentials: "include",
      headers: {
        Accept: "application/json",
        "X-Requested-With": "burnrate",
        ...(init?.body ? { "Content-Type": "application/json" } : {}),
        ...init?.headers,
      },
    });
  } catch {
    throw new ApiError(`Can't reach the Burnrate API at ${API_URL}`, 0, url);
  }
  if (res.status === 401 && !path.startsWith("/api/auth/")) redirectToLogin();
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = (await res.json()) as { detail?: unknown };
      if (body?.detail) detail = typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail);
    } catch {
      /* non-JSON error body */
    }
    throw new ApiError(`${res.status} ${detail}`.trim(), res.status, url);
  }
  if (res.status === 204) return undefined as T;
  const text = await res.text();
  return (text ? JSON.parse(text) : undefined) as T;
}

const fetcher = <T,>(path: string) => request<T>(path);

const defaultSWR: SWRConfiguration = {
  revalidateOnFocus: false,
  // No keepPreviousData: showing the previous dimension/window's numbers under new labels (or after a
  // failed fetch) is worse than a brief skeleton.
  shouldRetryOnError: true,
  errorRetryCount: 2,
};

/** Generic SWR hook keyed by API path. Pass `null` to skip. */
export function useApi<T>(path: string | null, config?: SWRConfiguration<T, ApiError>) {
  return useSWR<T, ApiError>(path, fetcher<T>, { ...defaultSWR, ...config });
}

/* ------------------------------------------------------------------ */
/* Endpoint helpers                                                    */
/* ------------------------------------------------------------------ */

export type EventsQuery = {
  limit: number;
  offset: number;
  provider?: string;
  model?: string;
  project?: string;
  feature?: string;
  customer?: string;
};

export const paths = {
  meta: () => "/api/meta",
  overview: (days: number) => buildPath("/api/overview", { days }),
  breakdown: (dimension: Dimension, days: number, limit = 10) => buildPath("/api/breakdown", { dimension, days, limit }),
  timeseries: (group_by: Dimension, days: number) => buildPath("/api/timeseries", { group_by, days }),
  events: (q: EventsQuery) =>
    buildPath("/api/events", q),
  anomalies: (days: number) => buildPath("/api/anomalies", { days }),
  forecast: () => "/api/forecast",
  reconciliation: (days: number) => buildPath("/api/reconciliation", { days }),
  budgets: () => "/api/budgets",
  insights: () => "/api/insights",
  pricing: () => "/api/pricing",
  alerts: (days: number) => buildPath("/api/alerts", { days }),
};

export const useMeta = () => useApi<Meta>(paths.meta());
export const useOverview = (days: number) => useApi<Overview>(paths.overview(days));
export const useBreakdown = (dimension: Dimension, days: number, limit = 10) =>
  useApi<Breakdown>(paths.breakdown(dimension, days, limit));
export const useTimeseries = (groupBy: Dimension, days: number) => useApi<Timeseries>(paths.timeseries(groupBy, days));
export const useEvents = (q: Parameters<typeof paths.events>[0]) => useApi<EventsPage>(paths.events(q));
export const useAnomalies = (days: number) => useApi<AnomaliesResponse>(paths.anomalies(days));
export const useForecast = () => useApi<Forecast>(paths.forecast());
export const useReconciliation = (days: number) => useApi<Reconciliation>(paths.reconciliation(days));
export const useBudgets = () => useApi<BudgetsResponse>(paths.budgets());
export const useInsights = () => useApi<InsightsResponse>(paths.insights());
export const usePricing = () => useApi<PricingResponse>(paths.pricing());

export const useAlerts = (days: number) => useApi<AlertsResponse>(paths.alerts(days));
export const evaluateAlerts = () => request<{ fired: number }>("/api/alerts/evaluate", { method: "POST" });

export const createBudget = (body: BudgetCreate) =>
  request<Budget>(paths.budgets(), { method: "POST", body: JSON.stringify(body) });

export const deleteBudget = (id: Budget["id"]) =>
  request<void>(`/api/budgets/${encodeURIComponent(String(id))}`, { method: "DELETE" });

/* ------------------------------------------------------------------ */
/* Admin: settings, users, keys                                        */
/* ------------------------------------------------------------------ */

export interface AlertSettings {
  connected: boolean;
  source: "dashboard" | "environment" | null;
  webhook_hint: string | null;
  is_slack: boolean | null;
  timer: { enabled: boolean; interval_minutes: number; last_run_at: string | null; last_run_error: string | null };
  last_delivered_at: string | null;
  last_failure: { at: string; error: string; title: string } | null;
  undelivered: number;
}

export const useAlertSettings = () => useApi<AlertSettings>("/api/settings/alerts");
export const saveWebhook = (webhook_url: string) =>
  request<AlertSettings>("/api/settings/alerts", { method: "PUT", body: JSON.stringify({ webhook_url }) });
export const disconnectWebhook = () => request<AlertSettings>("/api/settings/alerts", { method: "DELETE" });
export const sendTestAlert = () => request<{ sent: boolean }>("/api/settings/alerts/test", { method: "POST" });

export interface AdminUser {
  id: number;
  email: string;
  name: string | null;
  role: "admin" | "member" | "viewer";
  teams: string[] | null;
  created_at: string;
  last_login_at: string | null;
  disabled: boolean;
  invite_pending: boolean;
}

export interface InviteLink {
  user: AdminUser;
  invite_path: string;
  invite_expires_at: string;
}

export const useUsers = () => useApi<{ items: AdminUser[] }>("/api/users");
export const inviteUser = (body: { email: string; name?: string; role: string; teams: string[] | null }) =>
  request<InviteLink>("/api/users", { method: "POST", body: JSON.stringify(body) });
export const updateUser = (id: number, body: Partial<Pick<AdminUser, "name" | "role" | "teams" | "disabled">>) =>
  request<{ user: AdminUser }>(`/api/users/${id}`, { method: "PATCH", body: JSON.stringify(body) });
export const newInviteLink = (id: number) => request<InviteLink>(`/api/users/${id}/invite`, { method: "POST" });

export interface IngestKey {
  id: number;
  name: string;
  prefix: string;
  created_at: string;
  last_used_at: string | null;
  revoked_at: string | null;
}

export const useKeys = () => useApi<{ items: IngestKey[] }>("/api/keys");
export const createKey = (name: string) =>
  request<{ key: IngestKey; secret: string }>("/api/keys", { method: "POST", body: JSON.stringify({ name }) });
export const revokeKey = (id: number) => request<{ key: IngestKey }>(`/api/keys/${id}/revoke`, { method: "POST" });
/** Only revoked keys can be deleted. */
export const deleteKey = (id: number) => request<void>(`/api/keys/${id}`, { method: "DELETE" });

export interface JiraSettings {
  connected: boolean;
  site: string | null;
  email: string | null;
  token_hint: string | null;
  project_key: string | null;
  issue_type: string | null;
  min_severity: "high" | "medium";
  last_ticket: { key: string; url: string | null; title: string } | null;
  last_failure: { error: string; title: string } | null;
}

export const useJiraSettings = () => useApi<JiraSettings>("/api/settings/jira");
export const connectJira = (body: {
  site: string;
  email: string;
  api_token: string | null;
  project_key: string;
  issue_type: string;
  min_severity: "high" | "medium";
}) =>
  request<JiraSettings & { verified: { account: string | null; project_name: string | null; issue_types: string[] } }>(
    "/api/settings/jira",
    { method: "PUT", body: JSON.stringify(body) },
  );
export const disconnectJira = () => request<JiraSettings>("/api/settings/jira", { method: "DELETE" });
export const createJiraTestTicket = () => request<{ key: string; url: string }>("/api/settings/jira/test", { method: "POST" });
