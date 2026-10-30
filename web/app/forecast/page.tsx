"use client";

import { CalendarClock, Gauge, ShieldCheck, Target } from "lucide-react";
import { useMemo, useState } from "react";
import { Area, CartesianGrid, ComposedChart, Line, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { axisProps, gridProps, Legend, TooltipCard, type TooltipRow } from "@/components/charts";
import { KpiTile } from "@/components/kpi";
import { SeverityBadge } from "@/components/severity";
import {
  Card,
  CardBody,
  CardHeader,
  ChartSkeleton,
  DataState,
  EmptyState,
  ErrorState,
  PageHeader,
  Segmented,
  Table,
  TableSkeleton,
  Td,
  Th,
} from "@/components/ui";
import { useAnomalies, useBudgets, useForecast, type Forecast, type Severity } from "@/lib/api";
import { day, dayLong, monthLabel, pct, titleCase, usd, usdCompact } from "@/lib/format";
import { useDays } from "@/lib/use-days";

type Mode = "cumulative" | "daily";

interface Row {
  date: string;
  actual: number | null;
  forecast: number | null;
  band: [number, number] | null;
}

function buildRows(f: Forecast, mode: Mode): Row[] {
  const days = [...f.daily].sort((a, b) => a.date.localeCompare(b.date));
  if (mode === "daily") {
    const lastActualIdx = days.reduce((acc, d, i) => (d.actual_usd !== null ? i : acc), -1);
    return days.map((d, i) => ({
      date: d.date,
      actual: d.actual_usd,
      // bridge the dashed line from the last actual point
      forecast: d.forecast_usd ?? (i === lastActualIdx && days.some((x) => x.forecast_usd !== null) ? d.actual_usd : null),
      band: null,
    }));
  }
  const remaining = days.filter((d) => d.actual_usd === null && d.forecast_usd !== null).length;
  const lowGap = Math.max(0, f.projected_usd - f.low_usd);
  const highGap = Math.max(0, f.high_usd - f.projected_usd);
  let cum = 0;
  let step = 0;
  let lastActualIdx = -1;
  const rows: Row[] = days.map((d, i) => {
    if (d.actual_usd !== null) {
      cum += d.actual_usd;
      lastActualIdx = i;
      return { date: d.date, actual: cum, forecast: null, band: null };
    }
    cum += d.forecast_usd ?? 0;
    step += 1;
    const spread = remaining ? Math.sqrt(step / remaining) : 0;
    return { date: d.date, actual: null, forecast: cum, band: [Math.max(0, cum - lowGap * spread), cum + highGap * spread] };
  });
  if (lastActualIdx >= 0 && remaining > 0) {
    const r = rows[lastActualIdx];
    rows[lastActualIdx] = { ...r, forecast: r.actual, band: [r.actual ?? 0, r.actual ?? 0] };
  }
  return rows;
}

export default function ForecastPage() {
  const [days] = useDays();
  const forecast = useForecast();
  const f = forecast.data;
  const remainingDays = f ? f.daily.filter((d) => d.actual_usd === null).length : 0;

  return (
    <>
      <PageHeader title="Anomalies & Forecast" description={f ? `Month-end projection for ${monthLabel(f.month)} and unusual spend in the last ${days} days.` : "Where spend is heading, and what looks unusual."} />

      {forecast.error && !f ? (
        <Card className="mb-6">
          <ErrorState error={forecast.error} onRetry={() => void forecast.mutate()} />
        </Card>
      ) : (
        <div className="mb-6 grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-4">
          <KpiTile label="Month to date" icon={<CalendarClock className="size-4" aria-hidden />} loading={!f} value={usdCompact(f?.month_to_date_usd)} sub={f ? monthLabel(f.month) : undefined} />
          <KpiTile
            label="Projected month-end"
            icon={<Target className="size-4" aria-hidden />}
            loading={!f}
            value={usdCompact(f?.projected_usd)}
            sub={f && f.month_to_date_usd ? `${pct(((f.projected_usd - f.month_to_date_usd) / f.month_to_date_usd) * 100)} still to come` : undefined}
          />
          <KpiTile
            label="90% interval"
            icon={<ShieldCheck className="size-4" aria-hidden />}
            loading={!f}
            value={f ? `${usdCompact(f.low_usd)} – ${usdCompact(f.high_usd)}` : "—"}
            sub={<span className="line-clamp-2" title={f?.method}>{f?.method ? titleCase(f.method).replace(/ X /g, " × ") : "Linear trend over trailing history"}</span>}
          />
          <KpiTile
            label="Days remaining"
            icon={<Gauge className="size-4" aria-hidden />}
            loading={!f}
            value={String(remainingDays)}
            sub={f && remainingDays ? `~${usdCompact((f.projected_usd - f.month_to_date_usd) / remainingDays)} / day expected` : "Month complete"}
          />
        </div>
      )}

      <ForecastChart query={forecast} />
      <AnomaliesTable days={days} />
    </>
  );
}

function ForecastChart({ query }: { query: ReturnType<typeof useForecast> }) {
  const [mode, setMode] = useState<Mode>("cumulative");
  const budgets = useBudgets();
  const globalLimit = budgets.data?.items.find((b) => !b.scope_dimension)?.monthly_limit_usd;
  const rows = useMemo(() => (query.data ? buildRows(query.data, mode) : []), [query.data, mode]);

  return (
    <Card className="mb-6">
      <CardHeader
        title="Spend forecast"
        description={mode === "cumulative" ? "Cumulative month spend with forecast range" : "Daily spend, actual vs forecast"}
        actions={
          <Segmented
            size="sm"
            ariaLabel="Forecast view"
            value={mode}
            onChange={setMode}
            options={[
              { value: "cumulative", label: "Cumulative" },
              { value: "daily", label: "Daily" },
            ]}
          />
        }
      />
      <CardBody>
        <DataState query={query} loading={<ChartSkeleton height={320} />} isEmpty={(d) => d.daily.length === 0} empty={<EmptyState title="Not enough history to forecast" />}>
          {() => (
            <div className="space-y-3">
              <Legend
                items={[
                  { key: "a", label: "Actual", color: "var(--accent)" },
                  { key: "f", label: "Forecast", color: "var(--series-1)", dashed: true },
                  ...(mode === "cumulative" ? [{ key: "b", label: "90% range", color: "color-mix(in oklab, var(--series-1) 30%, transparent)" }] : []),
                  ...(mode === "cumulative" && globalLimit ? [{ key: "l", label: "Global budget", color: "var(--bad)", dashed: true }] : []),
                ]}
              />
              <div className="-ml-2 h-[320px]">
                <ResponsiveContainer width="100%" height="100%">
                  <ComposedChart data={rows} margin={{ top: 8, right: 12, bottom: 0, left: 0 }}>
                    <defs>
                      <linearGradient id="fc-actual" x1="0" y1="0" x2="0" y2="1">
                        <stop offset="0%" stopColor="var(--accent)" stopOpacity={0.25} />
                        <stop offset="100%" stopColor="var(--accent)" stopOpacity={0} />
                      </linearGradient>
                    </defs>
                    <CartesianGrid {...gridProps} />
                    <XAxis dataKey="date" {...axisProps} tickFormatter={day} minTickGap={28} />
                    <YAxis
                      {...axisProps}
                      width={56}
                      tickFormatter={(v: number) => usdCompact(v)}
                      domain={[0, (max: number) => (mode === "cumulative" && globalLimit ? Math.max(max, globalLimit * 1.05) : max)]}
                    />
                    <Tooltip
                      cursor={{ stroke: "var(--border-strong)" }}
                      content={({ active, payload, label }) => {
                        const r = active ? (payload?.[0]?.payload as Row | undefined) : undefined;
                        if (!r) return null;
                        const out: TooltipRow[] = [];
                        if (r.actual !== null) out.push({ key: "a", label: "Actual", value: usd(r.actual), color: "var(--accent)" });
                        if (r.forecast !== null && r.actual === null) out.push({ key: "f", label: "Forecast", value: usd(r.forecast), color: "var(--series-1)", dashed: true });
                        if (r.band && r.actual === null) out.push({ key: "b", label: "Range", value: `${usdCompact(r.band[0])} – ${usdCompact(r.band[1])}`, muted: true });
                        return <TooltipCard title={dayLong(String(label))} rows={out} />;
                      }}
                    />
                    {mode === "cumulative" ? (
                      <Area dataKey="band" stroke="none" fill="var(--series-1)" fillOpacity={0.14} isAnimationActive={false} connectNulls={false} activeDot={false} />
                    ) : null}
                    <Area
                      type="monotone"
                      dataKey="actual"
                      stroke="var(--accent)"
                      strokeWidth={2}
                      fill="url(#fc-actual)"
                      isAnimationActive={false}
                      connectNulls={false}
                      activeDot={{ r: 4, strokeWidth: 2, stroke: "var(--surface)" }}
                    />
                    <Line
                      type="monotone"
                      dataKey="forecast"
                      stroke="var(--series-1)"
                      strokeWidth={2}
                      strokeDasharray="5 4"
                      dot={false}
                      isAnimationActive={false}
                      connectNulls={false}
                      activeDot={{ r: 4, strokeWidth: 2, stroke: "var(--surface)" }}
                    />
                    {mode === "cumulative" && globalLimit ? (
                      <ReferenceLine
                        y={globalLimit}
                        stroke="var(--bad)"
                        strokeDasharray="3 3"
                        label={{ value: `Budget ${usdCompact(globalLimit)}`, position: "insideTopLeft", fill: "var(--bad)", fontSize: 11 }}
                      />
                    ) : null}
                  </ComposedChart>
                </ResponsiveContainer>
              </div>
            </div>
          )}
        </DataState>
      </CardBody>
    </Card>
  );
}

const SEV_FILTERS = [
  { value: "all", label: "All" },
  { value: "high", label: "High" },
  { value: "medium", label: "Medium" },
  { value: "low", label: "Low" },
] as const;

function AnomaliesTable({ days }: { days: number }) {
  const q = useAnomalies(days);
  const [sev, setSev] = useState<Severity | "all">("all");
  const counts = useMemo(() => {
    const c = { high: 0, medium: 0, low: 0 } as Record<Severity, number>;
    for (const a of q.data?.items ?? []) c[a.severity] = (c[a.severity] ?? 0) + 1;
    return c;
  }, [q.data]);

  return (
    <Card>
      <CardHeader
        title="Anomalies"
        description={q.data ? `${q.data.items.length} detected · ${counts.high} high, ${counts.medium} medium, ${counts.low} low` : "Daily spend that deviates from the trailing baseline"}
        actions={<Segmented size="sm" ariaLabel="Severity" value={sev} onChange={setSev} options={SEV_FILTERS} />}
      />
      <CardBody>
        <DataState
          query={q}
          loading={<TableSkeleton rows={6} cols={6} />}
          isEmpty={(d) => d.items.filter((a) => sev === "all" || a.severity === sev).length === 0}
          empty={<EmptyState title="No anomalies" description={sev === "all" ? `Spend looked normal over the last ${days} days.` : `No ${sev}-severity anomalies in this window.`} />}
        >
          {(d) => (
            <Table>
              <thead>
                <tr>
                  <Th>Date</Th>
                  <Th>Severity</Th>
                  <Th>Scope</Th>
                  <Th className="hidden lg:table-cell">What happened</Th>
                  <Th align="right">Spend</Th>
                  <Th align="right" className="hidden sm:table-cell">
                    Baseline
                  </Th>
                  <Th align="right">Change</Th>
                  <Th align="right" className="hidden md:table-cell">
                    z-score
                  </Th>
                </tr>
              </thead>
              <tbody>
                {d.items
                  .filter((a) => sev === "all" || a.severity === sev)
                  .map((a) => (
                    <tr key={a.id} className="align-top hover:bg-surface-2/50">
                      <Td className="tnum text-xs text-muted">{day(a.date)}</Td>
                      <Td>
                        <SeverityBadge severity={a.severity} />
                      </Td>
                      <Td>
                        <span className="text-xs text-subtle">{a.dimension}</span>
                        <span className="ml-1.5 font-mono text-[12px] text-fg">{a.key}</span>
                      </Td>
                      <Td className="hidden min-w-72 whitespace-normal! text-fg-2 lg:table-cell">{a.message}</Td>
                      <Td align="right" className="font-medium text-fg">
                        {usd(a.cost_usd)}
                      </Td>
                      <Td align="right" className="hidden text-muted sm:table-cell">
                        {usd(a.baseline_usd)}
                      </Td>
                      <Td align="right" className="font-medium text-bad">
                        {pct(a.change_pct, 0)}
                      </Td>
                      <Td align="right" className="hidden text-fg-2 md:table-cell">
                        {Number.isFinite(a.z_score) ? a.z_score.toFixed(1) : "—"}σ
                      </Td>
                    </tr>
                  ))}
              </tbody>
            </Table>
          )}
        </DataState>
      </CardBody>
    </Card>
  );
}
