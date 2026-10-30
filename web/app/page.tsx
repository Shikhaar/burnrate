"use client";

import { Activity, ArrowRight, Coins, Gauge, Hash, TriangleAlert } from "lucide-react";
import Link from "next/link";
import { useMemo } from "react";
import { Donut, TimeseriesArea } from "@/components/charts";
import { KpiTile } from "@/components/kpi";
import { BudgetStatusBadge, budgetColor, SeverityBadge } from "@/components/severity";
import {
  Card,
  CardBody,
  CardHeader,
  ChartSkeleton,
  DataState,
  Delta,
  Dot,
  EmptyState,
  ErrorState,
  PageHeader,
  ShareBar,
  Skeleton,
  Table,
  TableSkeleton,
  Td,
  Th,
} from "@/components/ui";
import { useAnomalies, useBreakdown, useBudgets, useOverview, useTimeseries, type Dimension } from "@/lib/api";
import { isOther, providerColor, providerLabel, providerRank } from "@/lib/colors";
import { changePct, day, monthLabel, numCompact, share, usd, usdCompact } from "@/lib/format";
import { useDays } from "@/lib/use-days";

export default function OverviewPage() {
  const [days] = useDays();
  const overview = useOverview(days);
  const o = overview.data;
  const delta = o ? changePct(o.total_cost_usd, o.prev_period_cost_usd) : null;
  const daily = o?.daily ?? [];

  return (
    <>
      <PageHeader
        title="Overview"
        description={o ? `${day(o.period.start)} – ${day(o.period.end)} · last ${days} days` : `Last ${days} days of AI spend across every provider`}
      />

      {o?.unpriced?.length ? <UnpricedNotice items={o.unpriced} /> : null}

      {overview.error && !o ? (
        <Card className="mb-6">
          <ErrorState error={overview.error} onRetry={() => void overview.mutate()} />
        </Card>
      ) : (
        <div className="mb-6 grid grid-cols-2 gap-3 xl:grid-cols-4">
          <KpiTile
            label="Total spend"
            icon={<Coins className="size-4" aria-hidden />}
            loading={!o}
            value={usdCompact(o?.total_cost_usd)}
            sub={<Delta value={delta} suffix={`vs prev ${days}d`} />}
            spark={daily.map((d) => d.cost_usd)}
          />
          <KpiTile
            label="Tokens"
            icon={<Hash className="size-4" aria-hidden />}
            loading={!o}
            value={numCompact(o?.total_tokens)}
            sub={o && o.request_count ? `${numCompact(o.total_tokens / o.request_count)} avg / request` : undefined}
            spark={daily.map((d) => d.tokens)}
            sparkColor="var(--series-1)"
          />
          <KpiTile
            label="Requests"
            icon={<Activity className="size-4" aria-hidden />}
            loading={!o}
            value={numCompact(o?.request_count)}
            sub={o && o.request_count ? `${usd(o.total_cost_usd / o.request_count)} avg cost` : undefined}
            spark={daily.map((d) => d.requests)}
            sparkColor="var(--series-3)"
          />
          <KpiTile
            label="Projected month-end"
            icon={<Gauge className="size-4" aria-hidden />}
            loading={!o}
            value={usdCompact(o?.projected_month_cost_usd)}
            sub={o ? `${usdCompact(o.month_to_date_cost_usd)} month to date` : undefined}
          />
        </div>
      )}

      <div className="mb-6 grid grid-cols-1 gap-4 xl:grid-cols-3">
        <SpendByProviderChart days={days} />
        <ProviderSplit overview={overview} days={days} />
      </div>

      <div className="mb-6 grid grid-cols-1 gap-4 xl:grid-cols-2">
        <TopTable dimension="project" title="Top projects" days={days} />
        <TopTable dimension="model" title="Top models" days={days} />
      </div>

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2 xl:grid-cols-3">
        <LatestAnomalies days={days} />
        <BudgetSummary />
        <CodingAgents days={days} />
      </div>
    </>
  );
}

function SpendByProviderChart({ days }: { days: number }) {
  const ts = useTimeseries("provider", days);
  return (
    <Card className="xl:col-span-2">
      <CardHeader title="Daily spend by provider" description="Stacked USD cost per day" />
      <CardBody>
        <DataState
          query={ts}
          loading={<ChartSkeleton height={300} />}
          isEmpty={(d) => d.points.length === 0}
          empty={<EmptyState title="No spend recorded" description="Send events to /v1/events to see daily spend here." />}
        >
          {(d) => <TimeseriesArea data={d} height={280} />}
        </DataState>
      </CardBody>
    </Card>
  );
}

function TopTable({ dimension, title, days }: { dimension: Dimension; title: string; days: number }) {
  const q = useBreakdown(dimension, days, 6);
  return (
    <Card>
      <CardHeader
        title={title}
        actions={
          <Link href={`/explore?dim=${dimension}${days !== 30 ? `&days=${days}` : ""}`} className="flex items-center gap-1 text-xs text-muted hover:text-fg">
            Explore <ArrowRight className="size-3" aria-hidden />
          </Link>
        }
      />
      <CardBody>
        <DataState query={q} loading={<TableSkeleton rows={6} cols={4} />} isEmpty={(d) => d.items.length === 0} empty={<EmptyState title={`No ${dimension} data`} />}>
          {(d) => (
            <Table>
              <thead>
                <tr>
                  <Th>{dimension === "model" ? "Model" : "Project"}</Th>
                  <Th align="right">Spend</Th>
                  <Th className="hidden w-32 sm:table-cell">Share</Th>
                  <Th align="right">Change</Th>
                </tr>
              </thead>
              <tbody>
                {d.items.map((it) => (
                  <tr key={it.key} className="hover:bg-surface-2/50">
                    <Td className={isOther(it.key) ? "text-muted" : "font-medium text-fg"} mono={dimension === "model" && !isOther(it.key)}>
                      {isOther(it.key) ? "Other" : it.key}
                    </Td>
                    <Td align="right">{usd(it.cost_usd)}</Td>
                    <Td className="hidden sm:table-cell">
                      <div className="flex items-center gap-2">
                        <ShareBar value={it.share} />
                        <span className="tnum w-10 text-right text-xs text-muted">{share(it.share, 0)}</span>
                      </div>
                    </Td>
                    <Td align="right">{isOther(it.key) ? <span className="text-xs text-subtle">—</span> : <Delta value={it.change_pct} />}</Td>
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

function CodingAgents({ days }: { days: number }) {
  const q = useBreakdown("user", days, 50);
  const users = (q.data?.items ?? []).filter((u) => u.key !== "(unattributed)");
  const total = users.reduce((s, u) => s + u.cost_usd, 0);
  return (
    <Card>
      <CardHeader
        title="Coding agents"
        description={q.data ? `${usd(total)} across ${users.filter((u) => !isOther(u.key)).length} engineers` : "Spend by engineer"}
        actions={
          <Link href={`/explore?dim=user${days !== 30 ? `&days=${days}` : ""}`} className="flex items-center gap-1 text-xs text-muted hover:text-fg">
            Explore <ArrowRight className="size-3" aria-hidden />
          </Link>
        }
      />
      <CardBody>
        <DataState
          query={q}
          loading={<TableSkeleton rows={5} cols={2} />}
          isEmpty={() => users.length === 0}
          empty={<EmptyState title="No coding-agent usage" description="Engineer-attributed usage (e.g. Claude Code) appears here." />}
        >
          {() => (
            <ul className="space-y-3">
              {users.slice(0, 6).map((u) => (
                <li key={u.key}>
                  <div className="mb-1 flex items-center justify-between gap-2 text-sm">
                    <span className={isOther(u.key) ? "text-muted" : "truncate font-mono text-[12px] text-fg"}>{isOther(u.key) ? "Other" : u.key}</span>
                    <span className="tnum shrink-0 text-fg">
                      {usd(u.cost_usd)} <Delta value={isOther(u.key) ? null : u.change_pct} className="ml-1" />
                    </span>
                  </div>
                  <ShareBar value={total ? u.cost_usd / total : 0} color="var(--series-2)" />
                </li>
              ))}
            </ul>
          )}
        </DataState>
      </CardBody>
    </Card>
  );
}

function LatestAnomalies({ days }: { days: number }) {
  const q = useAnomalies(days);
  return (
    <Card>
      <CardHeader
        title="Latest anomalies"
        description="Unusual daily spend vs. trailing baseline"
        actions={
          <Link href="/forecast" className="flex items-center gap-1 text-xs text-muted hover:text-fg">
            View all <ArrowRight className="size-3" aria-hidden />
          </Link>
        }
      />
      <CardBody>
        <DataState
          query={q}
          compact
          loading={<TableSkeleton rows={4} cols={2} />}
          isEmpty={(d) => d.items.length === 0}
          empty={<EmptyState title="All quiet" description={`No anomalies detected in the last ${days} days.`} />}
        >
          {(d) => (
            <ul className="divide-y divide-border">
              {d.items.slice(0, 4).map((a) => (
                <li key={a.id} className="flex items-start gap-3 py-2.5 first:pt-0 last:pb-0">
                  <SeverityBadge severity={a.severity} />
                  <div className="min-w-0 flex-1">
                    <p className="text-sm text-fg">{a.message}</p>
                    <p className="mt-0.5 text-xs text-muted">
                      {day(a.date)} · {a.dimension}: <span className="font-mono">{a.key}</span>
                      {a.related?.length ? <> · also {a.related.join(", ")}</> : null}
                    </p>
                  </div>
                  <div className="text-right">
                    <p className="tnum text-sm font-medium text-fg">{usd(a.cost_usd)}</p>
                    <p className="tnum text-xs text-muted">vs {usd(a.baseline_usd)}</p>
                  </div>
                </li>
              ))}
            </ul>
          )}
        </DataState>
      </CardBody>
    </Card>
  );
}

function BudgetSummary() {
  const q = useBudgets();
  return (
    <Card>
      <CardHeader
        title="Budget utilization"
        description={q.data?.items.length ? `${q.data.items.length} budgets · ${monthLabel(new Date().toISOString().slice(0, 7))}` : "Monthly limits"}
        actions={
          <Link href="/budgets" className="flex items-center gap-1 text-xs text-muted hover:text-fg">
            Manage <ArrowRight className="size-3" aria-hidden />
          </Link>
        }
      />
      <CardBody>
        <DataState
          query={q}
          compact
          loading={<TableSkeleton rows={4} cols={2} />}
          isEmpty={(d) => d.items.length === 0}
          empty={
            <EmptyState
              title="No budgets yet"
              description="Set monthly limits per project, team or provider."
              action={
                <Link href="/budgets" className="text-xs font-medium text-accent hover:underline">
                  Create a budget
                </Link>
              }
            />
          }
        >
          {(d) => (
            <ul className="space-y-3.5">
              {[...d.items]
                .sort((a, b) => b.utilization - a.utilization)
                .slice(0, 5)
                .map((b) => (
                  <li key={b.id}>
                    <div className="mb-1.5 flex items-center justify-between gap-2 text-sm">
                      <span className="flex min-w-0 items-center gap-2">
                        <span className="truncate font-medium text-fg">{b.name}</span>
                        <BudgetStatusBadge status={b.status} />
                      </span>
                      <span className="tnum shrink-0 text-xs text-muted">
                        <span className="text-fg">{usdCompact(b.spent_usd)}</span> / {usdCompact(b.monthly_limit_usd)}
                      </span>
                    </div>
                    <ShareBar value={b.utilization} color={budgetColor(b.status)} />
                  </li>
                ))}
            </ul>
          )}
        </DataState>
      </CardBody>
    </Card>
  );
}

function ProviderSplit({ overview, days }: { overview: ReturnType<typeof useOverview>; days: number }) {
  const o = overview.data;
  const items = useMemo(
    () =>
      [...(o?.by_provider ?? [])]
        .sort((a, b) => b.cost_usd - a.cost_usd)
        .map((p) => ({ key: p.key, label: providerLabel(p.key), value: p.cost_usd, color: providerColor(p.key) })),
    [o],
  );
  const total = items.reduce((s, i) => s + i.value, 0);
  return (
    <Card>
      <CardHeader title="Spend by provider" description={`Share of ${days}-day spend`} />
      <CardBody>
        <DataState
          query={overview}
          loading={<Skeleton className="mx-auto size-44 rounded-full" />}
          isEmpty={(d) => d.by_provider.length === 0}
          empty={<EmptyState title="No provider spend" description="No events in this window yet." />}
        >
          {() => (
            <div className="flex flex-col gap-5 sm:flex-row sm:items-center xl:flex-col xl:items-stretch">
              <Donut items={items} centerLabel="Total" centerValue={usdCompact(total)} size={168} />
              <ul className="flex-1 space-y-2.5">
                {[...items]
                  .sort((a, b) => providerRank(a.key) - providerRank(b.key))
                  .map((it) => (
                    <li key={it.key} className="text-sm">
                      <div className="flex items-center justify-between gap-2">
                        <span className="flex items-center gap-2 text-fg-2">
                          <Dot color={it.color} />
                          {it.label}
                        </span>
                        <span className="tnum text-fg">
                          {usd(it.value)} <span className="ml-1 text-xs text-muted">{share(total ? it.value / total : 0, 0)}</span>
                        </span>
                      </div>
                    </li>
                  ))}
              </ul>
            </div>
          )}
        </DataState>
      </CardBody>
    </Card>
  );
}

function UnpricedNotice({ items }: { items: { provider: string; model: string; requests: number }[] }) {
  const total = items.reduce((n, i) => n + i.requests, 0);
  const shown = items.slice(0, 3).map((i) => `${i.provider}/${i.model}`).join(", ");
  return (
    <div
      role="status"
      className="mb-6 flex items-start gap-3 rounded-xl border border-amber-500/30 bg-amber-500/10 px-4 py-3 text-sm text-fg"
    >
      <TriangleAlert className="mt-0.5 size-4 shrink-0 text-amber-500" aria-hidden />
      <p>
        <span className="font-medium">
          {total.toLocaleString()} {total === 1 ? "request has" : "requests have"} no price
        </span>{" "}
        and {total === 1 ? "counts" : "count"} as $0:{" "}
        <span className="font-mono text-xs">{shown}</span>
        {items.length > 3 ? ` and ${items.length - 3} more` : ""}. Add them to the price registry, then run{" "}
        <code className="font-mono text-xs">burnrate pricing</code>.{" "}
        <Link href="/pricing" className="text-muted underline-offset-2 hover:text-fg hover:underline">
          View registry
        </Link>
      </p>
    </div>
  );
}
