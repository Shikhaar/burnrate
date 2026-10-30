"use client";

import { ArrowDownWideNarrow, DatabaseZap, Lightbulb, PiggyBank, Sparkles, TriangleAlert } from "lucide-react";
import type { ReactNode } from "react";
import { Badge, Card, DataState, EmptyState, PageHeader, ShareBar, Skeleton } from "@/components/ui";
import { useInsights, type Insight } from "@/lib/api";
import { titleCase, usd, usdCompact } from "@/lib/format";

const KIND: Record<string, { label: string; icon: ReactNode }> = {
  model_downgrade: { label: "Model routing", icon: <ArrowDownWideNarrow className="size-4" aria-hidden /> },
  prompt_caching: { label: "Prompt caching", icon: <DatabaseZap className="size-4" aria-hidden /> },
  failed_requests: { label: "Wasted spend", icon: <TriangleAlert className="size-4" aria-hidden /> },
};

function kindMeta(kind: string) {
  return KIND[kind] ?? { label: titleCase(kind), icon: <Lightbulb className="size-4" aria-hidden /> };
}

function scopeText(scope: Insight["scope"]): string | null {
  if (!scope) return null;
  if (typeof scope === "string") return scope;
  return Object.entries(scope)
    .map(([k, v]) => `${k}: ${String(v)}`)
    .join(" · ");
}

export default function InsightsPage() {
  const q = useInsights();
  const items = [...(q.data?.items ?? [])].sort((a, b) => b.estimated_monthly_savings_usd - a.estimated_monthly_savings_usd);
  const total = items.reduce((s, i) => s + (i.estimated_monthly_savings_usd || 0), 0);
  const max = items[0]?.estimated_monthly_savings_usd || 1;

  return (
    <>
      <PageHeader title="Insights" description="Optimization opportunities found in your traffic, ranked by estimated monthly savings." />

      <Card className="relative mb-6 overflow-hidden p-5 sm:p-6">
        <div className="pointer-events-none absolute -right-16 -top-24 size-64 rounded-full bg-accent/10 blur-3xl" aria-hidden />
        <div className="relative flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
          <div className="flex items-center gap-4">
            <div className="grid size-12 place-items-center rounded-xl bg-accent-soft text-accent">
              <PiggyBank className="size-6" aria-hidden />
            </div>
            <div>
              <p className="text-xs font-medium uppercase tracking-wide text-muted">Total potential savings</p>
              {q.data ? (
                <p className="tnum text-3xl font-semibold tracking-tight text-fg">
                  {usd(total)}
                  <span className="ml-1 text-base font-normal text-muted">/ month</span>
                </p>
              ) : q.error ? (
                <p className="text-3xl font-semibold text-subtle">—</p>
              ) : (
                <Skeleton className="mt-1 h-9 w-40" />
              )}
            </div>
          </div>
          {q.data ? (
            <div className="flex flex-wrap gap-2 text-xs text-muted">
              <Badge tone="accent" icon={<Sparkles className="size-3" aria-hidden />}>
                {items.length} recommendations
              </Badge>
              <Badge>{usdCompact(total * 12)} / year</Badge>
            </div>
          ) : null}
        </div>
      </Card>

      <DataState
        query={q}
        compact={false}
        loading={
          <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
            {Array.from({ length: 4 }).map((_, i) => (
              <Card key={i} className="p-5">
                <Skeleton className="h-4 w-24" />
                <Skeleton className="mt-3 h-5 w-3/4" />
                <Skeleton className="mt-3 h-12 w-full" />
              </Card>
            ))}
          </div>
        }
        isEmpty={(d) => d.items.length === 0}
        empty={
          <Card>
            <EmptyState icon={<Sparkles className="size-5" aria-hidden />} title="No recommendations right now" description="Burnrate will surface model routing, caching and waste opportunities as traffic accumulates." />
          </Card>
        }
      >
        {() => (
          <ol className="grid grid-cols-1 gap-4 lg:grid-cols-2">
            {items.map((it, idx) => {
              const k = kindMeta(it.kind);
              const scope = scopeText(it.scope);
              return (
                <li key={it.id}>
                  <Card className="flex h-full flex-col p-5 transition-colors hover:border-border-strong">
                    <div className="flex items-start justify-between gap-3">
                      <div className="flex items-center gap-2 text-xs text-muted">
                        <span className="grid size-7 place-items-center rounded-lg bg-surface-2 text-fg-2">{k.icon}</span>
                        <span className="font-medium">{k.label}</span>
                        <span className="text-subtle">#{idx + 1}</span>
                      </div>
                      <div className="text-right">
                        <p className="tnum text-lg font-semibold text-ok">{usd(it.estimated_monthly_savings_usd)}</p>
                        <p className="text-[11px] text-subtle">est. / month</p>
                      </div>
                    </div>
                    <h3 className="mt-3 text-sm font-semibold leading-snug text-fg">{it.title}</h3>
                    <p className="mt-1.5 flex-1 text-sm leading-relaxed text-muted">{it.detail}</p>
                    <div className="mt-4 flex items-center gap-3">
                      {scope ? <span className="truncate rounded-md bg-surface-2 px-1.5 py-0.5 font-mono text-[11px] text-fg-2">{scope}</span> : null}
                      <div className="ml-auto w-28 shrink-0">
                        <ShareBar value={it.estimated_monthly_savings_usd / max} color="var(--ok)" />
                      </div>
                    </div>
                  </Card>
                </li>
              );
            })}
          </ol>
        )}
      </DataState>
    </>
  );
}
