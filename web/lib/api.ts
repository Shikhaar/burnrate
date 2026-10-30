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

export async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const url = `${API_URL}${path}`;
  let res: Response;
  try {
    res = await fetch(url, {
      ...init,
      headers: { Accept: "application/json", ...(init?.body ? { "Content-Type": "application/json" } : {}), ...init?.headers },
    });
  } catch {
    throw new ApiError(`Can't reach the Burnrate API at ${API_URL}`, 0, url);
  }
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
  keepPreviousData: true,
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
