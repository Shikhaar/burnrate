"use client";

import { CheckCircle2, CircleHelp, Receipt, Scale, SearchCheck } from "lucide-react";
import { useMemo, useState } from "react";
import { CartesianGrid, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { axisProps, gridProps, Legend, TooltipCard } from "@/components/charts";
import { KpiTile } from "@/components/kpi";
import {
  Badge,
  Card,
  CardBody,
  CardHeader,
  ChartSkeleton,
  DataState,
  Dot,
  EmptyState,
  ErrorState,
  PageHeader,
  Segmented,
  Table,
  TableSkeleton,
  Td,
  Th,
} from "@/components/ui";
import { useReconciliation, type Reconciliation, type ReconciliationStatus } from "@/lib/api";
import { providerColor, providerLabel, providerRank } from "@/lib/colors";
import { changePct, day, dayLong, pct, usd, usdCompact } from "@/lib/format";
import { useDays } from "@/lib/use-days";

function StatusBadge({ status }: { status: ReconciliationStatus | string }) {
  if (status === "ok")
    return (
      <Badge tone="ok" icon={<CheckCircle2 className="size-3" aria-hidden />}>
        Within tolerance
      </Badge>
    );
  if (status === "investigate")
    return (
      <Badge tone="warn" icon={<SearchCheck className="size-3" aria-hidden />}>
        Investigate
      </Badge>
    );
  return (
    <Badge icon={<CircleHelp className="size-3" aria-hidden />}>
      No billing data
    </Badge>
  );
}

export default function ReconciliationPage() {
  const [days] = useDays();
  const q = useReconciliation(days);
  const d = q.data;

  const totals = useMemo(() => {
    const billedItems = (d?.items ?? []).filter((i) => i.status !== "no_billing_data");
    const est = billedItems.reduce((s, i) => s + (i.estimated_usd ?? 0), 0);
    const billed = billedItems.reduce((s, i) => s + (i.billed_usd ?? 0), 0);
    return { est, billed, delta: billed - est, deltaPct: changePct(billed, est) };
  }, [d]);
  const flagged = d?.items.filter((i) => i.status === "investigate").length ?? 0;

  return (
    <>
      <PageHeader
        title="Reconciliation"
        description="Burnrate's token-based cost estimates compared with what providers actually billed."
      />

      {q.error && !d ? (
        <Card className="mb-6">
          <ErrorState error={q.error} onRetry={() => void q.mutate()} />
        </Card>
      ) : (
        <div className="mb-6 grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-4">
          <KpiTile label="Estimated" icon={<Receipt className="size-4" aria-hidden />} loading={!d} value={usdCompact(totals.est)} sub="From metered tokens × price registry" />
          <KpiTile label="Billed" icon={<Receipt className="size-4" aria-hidden />} loading={!d} value={usdCompact(totals.billed)} sub="From provider usage/billing APIs" />
          <KpiTile
            label="Net variance"
            icon={<Scale className="size-4" aria-hidden />}
            loading={!d}
            value={`${totals.delta > 0 ? "+" : ""}${usdCompact(totals.delta)}`}
            sub={totals.deltaPct === null ? "—" : `${pct(totals.deltaPct)} billed vs estimated`}
          />
          <KpiTile
            label="Providers to investigate"
            icon={<SearchCheck className="size-4" aria-hidden />}
            loading={!d}
            value={String(flagged)}
            sub={d ? `Tolerance ±${d.tolerance_pct}%` : undefined}
          />
        </div>
      )}

      <Card className="mb-6">
        <CardHeader title="By provider" description={`Last ${days} days · totals cover only days the provider has billed`} />
        <CardBody>
          <DataState query={q} loading={<TableSkeleton rows={4} cols={6} />} isEmpty={(r) => r.items.length === 0} empty={<EmptyState title="Nothing to reconcile" description="Connect provider billing to compare estimates with invoices." />}>
            {(r) => (
              <Table>
                <thead>
                  <tr>
                    <Th>Provider</Th>
                    <Th align="right">Estimated</Th>
                    <Th align="right">Billed</Th>
                    <Th align="right">Delta</Th>
                    <Th align="right">Delta %</Th>
                    <Th>Status</Th>
                  </tr>
                </thead>
                <tbody>
                  {[...r.items]
                    .sort((a, b) => providerRank(a.provider) - providerRank(b.provider))
                    .map((it) => {
                      const over = (it.delta_pct ?? 0) > 0;
                      const outside = it.delta_pct !== null && Math.abs(it.delta_pct) > r.tolerance_pct;
                      return (
                        <tr key={it.provider} className="hover:bg-surface-2/50">
                          <Td className="font-medium text-fg">
                            <span className="flex items-center gap-2">
                              <Dot color={providerColor(it.provider)} />
                              {providerLabel(it.provider)}
                            </span>
                          </Td>
                          <Td align="right">{usd(it.estimated_usd)}</Td>
                          <Td align="right">{usd(it.billed_usd)}</Td>
                          <Td align="right" className={outside ? (over ? "text-bad" : "text-warn") : "text-fg-2"}>
                            {it.delta_usd === null ? "—" : `${it.delta_usd > 0 ? "+" : ""}${usd(it.delta_usd)}`}
                          </Td>
                          <Td align="right" className={outside ? (over ? "font-medium text-bad" : "font-medium text-warn") : "text-fg-2"}>
                            {pct(it.delta_pct, 2)}
                          </Td>
                          <Td>
                            <StatusBadge status={it.status} />
                          </Td>
                        </tr>
                      );
                    })}
                </tbody>
              </Table>
            )}
          </DataState>
        </CardBody>
      </Card>

      <DailyChart query={q} />
    </>
  );
}

function DailyChart({ query }: { query: ReturnType<typeof useReconciliation> }) {
  const [provider, setProvider] = useState<string>("all");
  const providers = useMemo(
    () => [...new Set((query.data?.daily ?? []).map((d) => d.provider))].sort((a, b) => providerRank(a) - providerRank(b)),
    [query.data],
  );
  const rows = useMemo(() => (query.data ? aggregate(query.data, provider) : []), [query.data, provider]);
  const estColor = provider === "all" ? "var(--accent)" : providerColor(provider);

  return (
    <Card>
      <CardHeader
        title="Daily estimated vs billed"
        description="Gaps mean the provider has not reported billing for that day yet"
        actions={
          providers.length > 1 ? (
            <Segmented
              size="sm"
              ariaLabel="Provider"
              value={provider}
              onChange={setProvider}
              options={[{ value: "all", label: "All" }, ...providers.map((p) => ({ value: p, label: providerLabel(p) }))]}
            />
          ) : null
        }
      />
      <CardBody>
        <DataState query={query} loading={<ChartSkeleton height={300} />} isEmpty={(r) => r.daily.length === 0} empty={<EmptyState title="No daily data" />}>
          {() => (
            <div className="space-y-3">
              <Legend
                items={[
                  { key: "e", label: "Estimated", color: estColor },
                  { key: "b", label: "Billed", color: "var(--fg-2)", dashed: true },
                ]}
              />
              <div className="-ml-2 h-[300px]">
                <ResponsiveContainer width="100%" height="100%">
                  <LineChart data={rows} margin={{ top: 8, right: 12, bottom: 0, left: 0 }}>
                    <CartesianGrid {...gridProps} />
                    <XAxis dataKey="date" {...axisProps} tickFormatter={day} minTickGap={28} />
                    <YAxis {...axisProps} width={56} tickFormatter={(v: number) => usdCompact(v)} />
                    <Tooltip
                      cursor={{ stroke: "var(--border-strong)" }}
                      content={({ active, payload, label }) => {
                        const r = active ? (payload?.[0]?.payload as AggRow | undefined) : undefined;
                        if (!r) return null;
                        const delta = r.billed !== null ? r.billed - r.estimated : null;
                        return (
                          <TooltipCard
                            title={dayLong(String(label))}
                            rows={[
                              { key: "e", label: "Estimated", value: usd(r.estimated), color: estColor },
                              { key: "b", label: "Billed", value: r.billed === null ? "pending" : usd(r.billed), color: "var(--fg-2)", dashed: true },
                            ]}
                            footer={
                              <div className="flex justify-between">
                                <span className="text-muted">Delta</span>
                                <span className="tnum font-medium text-fg">
                                  {delta === null ? "—" : `${delta > 0 ? "+" : ""}${usd(delta)} (${pct(changePct(r.billed ?? 0, r.estimated), 1)})`}
                                </span>
                              </div>
                            }
                          />
                        );
                      }}
                    />
                    <Line type="monotone" dataKey="estimated" stroke={estColor} strokeWidth={2} dot={false} isAnimationActive={false} activeDot={{ r: 4, strokeWidth: 2, stroke: "var(--surface)" }} />
                    <Line
                      type="monotone"
                      dataKey="billed"
                      stroke="var(--fg-2)"
                      strokeWidth={2}
                      strokeDasharray="5 4"
                      dot={false}
                      connectNulls={false}
                      isAnimationActive={false}
                      activeDot={{ r: 4, strokeWidth: 2, stroke: "var(--surface)" }}
                    />
                  </LineChart>
                </ResponsiveContainer>
              </div>
            </div>
          )}
        </DataState>
      </CardBody>
    </Card>
  );
}

interface AggRow {
  date: string;
  estimated: number;
  billed: number | null;
}

/**
 * Sum providers per day. A day's billed total is null unless every included provider has billed it:
 * comparing a partial bill against the full estimate would show a false shortfall.
 */
function aggregate(r: Reconciliation, provider: string): AggRow[] {
  const map = new Map<string, AggRow & { complete: boolean }>();
  // "All" only sums providers that report billing, so estimated and billed stay comparable.
  const unbilled = new Set(r.items.filter((i) => i.status === "no_billing_data").map((i) => i.provider));
  for (const d of r.daily) {
    if (provider === "all" ? unbilled.has(d.provider) : d.provider !== provider) continue;
    const row = map.get(d.date) ?? { date: d.date, estimated: 0, billed: 0, complete: true };
    row.estimated += d.estimated_usd ?? 0;
    if (d.billed_usd === null || d.billed_usd === undefined) row.complete = false;
    else row.billed = (row.billed ?? 0) + d.billed_usd;
    map.set(d.date, row);
  }
  return [...map.values()]
    .map(({ complete, ...row }) => ({ ...row, billed: complete ? row.billed : null }))
    .sort((a, b) => a.date.localeCompare(b.date));
}
