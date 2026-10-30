"use client";

import clsx from "clsx";
import { useMemo, useState, type ReactNode } from "react";
import {
  Area,
  AreaChart,
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  Pie,
  PieChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import type { Timeseries } from "@/lib/api";
import { isOther, OTHER_COLOR, providerRank, seriesColors, seriesLabel } from "@/lib/colors";
import { day, dayLong, share as fmtShare, usd, usdCompact } from "@/lib/format";
import { Dot } from "./ui";

/* ---------------- Shared axis styling ---------------- */

export const axisProps = {
  stroke: "var(--subtle)",
  tick: { fill: "var(--muted)", fontSize: 11 },
  tickLine: false,
  axisLine: false,
} as const;

export const gridProps = {
  stroke: "var(--grid)",
  strokeDasharray: "0",
  vertical: false,
} as const;

/* ---------------- Tooltip ---------------- */

export interface TooltipRow {
  key: string;
  label: ReactNode;
  value: ReactNode;
  color?: string;
  dashed?: boolean;
  muted?: boolean;
}

export function TooltipCard({ title, rows, footer }: { title: ReactNode; rows: TooltipRow[]; footer?: ReactNode }) {
  return (
    <div className="min-w-44 rounded-lg border border-border bg-surface/95 px-3 py-2 text-xs shadow-xl shadow-black/10 backdrop-blur">
      <p className="mb-1.5 font-medium text-fg">{title}</p>
      <div className="space-y-1">
        {rows.map((r) => (
          <div key={r.key} className={clsx("flex items-center justify-between gap-4", r.muted && "text-muted")}>
            <span className="flex min-w-0 items-center gap-1.5 text-fg-2">
              {r.color ? (
                r.dashed ? (
                  <span className="inline-block h-0 w-2.5 border-t-2 border-dashed" style={{ borderColor: r.color }} aria-hidden />
                ) : (
                  <Dot color={r.color} />
                )
              ) : null}
              <span className="truncate">{r.label}</span>
            </span>
            <span className="tnum font-medium text-fg">{r.value}</span>
          </div>
        ))}
      </div>
      {footer ? <div className="mt-1.5 border-t border-border pt-1.5">{footer}</div> : null}
    </div>
  );
}

/* ---------------- Legend ---------------- */

export function Legend({
  items,
  hidden,
  onToggle,
}: {
  items: { key: string; label: ReactNode; color: string; dashed?: boolean }[];
  hidden?: Set<string>;
  onToggle?: (key: string) => void;
}) {
  return (
    <div className="flex flex-wrap items-center gap-x-3 gap-y-1.5">
      {items.map((it) => {
        const off = hidden?.has(it.key);
        const content = (
          <>
            {it.dashed ? (
              <span className="inline-block h-0 w-3 border-t-2 border-dashed" style={{ borderColor: it.color }} aria-hidden />
            ) : (
              <Dot color={it.color} />
            )}
            <span className="truncate">{it.label}</span>
          </>
        );
        return onToggle ? (
          <button
            key={it.key}
            type="button"
            onClick={() => onToggle(it.key)}
            aria-pressed={!off}
            className={clsx("flex max-w-48 items-center gap-1.5 text-xs transition-opacity", off ? "text-subtle line-through opacity-60" : "text-fg-2 hover:text-fg")}
          >
            {content}
          </button>
        ) : (
          <span key={it.key} className="flex max-w-48 items-center gap-1.5 text-xs text-fg-2">
            {content}
          </span>
        );
      })}
    </div>
  );
}

/* ---------------- Stacked area for timeseries ---------------- */

export function TimeseriesArea({
  data,
  height = 280,
  stacked = true,
  colorMap,
}: {
  data: Timeseries;
  height?: number;
  stacked?: boolean;
  /** Optional entity→color map so this chart matches a sibling chart's colors. */
  colorMap?: Record<string, string>;
}) {
  const dim = data.group_by;
  const keys = useMemo(() => {
    const totals = new Map<string, number>();
    for (const p of data.points) for (const [k, v] of Object.entries(p.values)) totals.set(k, (totals.get(k) ?? 0) + (v || 0));
    const ks = data.series_keys.length ? [...data.series_keys] : [...totals.keys()];
    // stable ordering: provider canonical order, otherwise by total desc; "other" always last (top of stack)
    return ks.sort((a, b) => {
      if (isOther(a) !== isOther(b)) return isOther(a) ? 1 : -1;
      if (dim === "provider") return providerRank(a) - providerRank(b) || a.localeCompare(b);
      return (totals.get(b) ?? 0) - (totals.get(a) ?? 0);
    });
  }, [data, dim]);
  const colors = useMemo(() => {
    const base = seriesColors(keys, dim);
    if (!colorMap) return base;
    const out: Record<string, string> = {};
    for (const k of keys) out[k] = colorMap[k] ?? (isOther(k) ? base[k] : OTHER_COLOR);
    return out;
  }, [keys, dim, colorMap]);
  const [hidden, setHidden] = useState<Set<string>>(new Set());

  const rows = useMemo(
    () =>
      data.points.map((p) => {
        const row: Record<string, number | string> = { date: p.date };
        for (const k of keys) row[k] = p.values[k] ?? 0;
        return row;
      }),
    [data.points, keys],
  );

  const toggle = (k: string) =>
    setHidden((prev) => {
      const next = new Set(prev);
      if (next.has(k)) next.delete(k);
      else next.add(k);
      return next;
    });

  return (
    <div className="space-y-3">
      <Legend items={keys.map((k) => ({ key: k, label: seriesLabel(k, dim), color: colors[k] }))} hidden={hidden} onToggle={toggle} />
      <div style={{ height }} className="-ml-2">
        <ResponsiveContainer width="100%" height="100%">
          <AreaChart data={rows} margin={{ top: 8, right: 8, bottom: 0, left: 0 }}>
            <defs>
              {keys.map((k, i) => (
                <linearGradient key={k} id={`ts-grad-${i}`} x1="0" y1="0" x2="0" y2="1">
                  <stop offset="0%" stopColor={colors[k]} stopOpacity={stacked ? 0.55 : 0.25} />
                  <stop offset="100%" stopColor={colors[k]} stopOpacity={stacked ? 0.25 : 0.02} />
                </linearGradient>
              ))}
            </defs>
            <CartesianGrid {...gridProps} />
            <XAxis dataKey="date" {...axisProps} tickFormatter={day} minTickGap={28} />
            <YAxis {...axisProps} width={56} tickFormatter={(v: number) => usdCompact(v)} />
            <Tooltip
              cursor={{ stroke: "var(--border-strong)", strokeWidth: 1 }}
              content={({ active, payload, label }) => {
                if (!active || !payload?.length) return null;
                const visible = keys.filter((k) => !hidden.has(k));
                const row = payload[0]?.payload as Record<string, number> | undefined;
                if (!row) return null;
                const total = visible.reduce((s, k) => s + (Number(row[k]) || 0), 0);
                const sorted = [...visible].sort((a, b) => (Number(row[b]) || 0) - (Number(row[a]) || 0));
                return (
                  <TooltipCard
                    title={dayLong(String(label))}
                    rows={sorted.map((k) => ({ key: k, label: seriesLabel(k, dim), value: usd(Number(row[k]) || 0), color: colors[k] }))}
                    footer={
                      <div className="flex justify-between text-fg">
                        <span className="text-muted">Total</span>
                        <span className="tnum font-semibold">{usd(total)}</span>
                      </div>
                    }
                  />
                );
              }}
            />
            {keys.map((k, i) => (
              <Area
                key={k}
                type="monotone"
                dataKey={k}
                stackId={stacked ? "1" : undefined}
                stroke={colors[k]}
                strokeWidth={1.5}
                fill={`url(#ts-grad-${i})`}
                hide={hidden.has(k)}
                isAnimationActive={false}
                activeDot={{ r: 4, strokeWidth: 2, stroke: "var(--surface)" }}
              />
            ))}
          </AreaChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
}

/* ---------------- Ranked horizontal bars ---------------- */

export function RankedBars({
  items,
  dimension,
  height,
}: {
  items: { key: string; cost_usd: number; share: number }[];
  dimension: string;
  height?: number;
}) {
  const colors = useMemo(() => seriesColors(items.map((i) => i.key), dimension), [items, dimension]);
  const h = height ?? Math.max(160, items.length * 34 + 16);
  const data = items.map((i) => ({ ...i, label: seriesLabel(i.key, dimension) }));
  return (
    <div style={{ height: h }}>
      <ResponsiveContainer width="100%" height="100%">
        <BarChart data={data} layout="vertical" margin={{ top: 0, right: 56, bottom: 0, left: 0 }} barCategoryGap={6}>
          <XAxis type="number" hide />
          <YAxis
            type="category"
            dataKey="label"
            {...axisProps}
            width={140}
            tick={{ fill: "var(--fg-2)", fontSize: 12 }}
            tickFormatter={(v: string) => (v.length > 20 ? `${v.slice(0, 19)}…` : v)}
          />
          <Tooltip
            cursor={{ fill: "var(--surface-2)" }}
            content={({ active, payload }) => {
              const p = active ? (payload?.[0]?.payload as (typeof data)[number] | undefined) : undefined;
              if (!p) return null;
              return (
                <TooltipCard
                  title={p.label}
                  rows={[
                    { key: "c", label: "Spend", value: usd(p.cost_usd), color: colors[p.key] },
                    { key: "s", label: "Share", value: fmtShare(p.share) },
                  ]}
                />
              );
            }}
          />
          <Bar
            dataKey="cost_usd"
            radius={[0, 4, 4, 0]}
            isAnimationActive={false}
            label={{ position: "right", fill: "var(--muted)", fontSize: 11, formatter: (v: unknown) => usdCompact(Number(v)) }}
          >
            {data.map((d) => (
              <Cell key={d.key} fill={colors[d.key]} />
            ))}
          </Bar>
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}

/* ---------------- Donut ---------------- */

export function Donut({
  items,
  centerLabel,
  centerValue,
  size = 180,
}: {
  items: { key: string; label: string; value: number; color: string }[];
  centerLabel: string;
  centerValue: string;
  size?: number;
}) {
  const total = items.reduce((s, i) => s + i.value, 0);
  return (
    <div className="relative mx-auto" style={{ width: size, height: size }}>
      <ResponsiveContainer width="100%" height="100%">
        <PieChart>
          <Pie
            data={items}
            dataKey="value"
            nameKey="label"
            innerRadius="72%"
            outerRadius="100%"
            paddingAngle={items.length > 1 ? 1.5 : 0}
            stroke="var(--surface)"
            strokeWidth={2}
            isAnimationActive={false}
            startAngle={90}
            endAngle={-270}
          >
            {items.map((it) => (
              <Cell key={it.key} fill={it.color} />
            ))}
          </Pie>
          <Tooltip
            content={({ active, payload }) => {
              const p = active ? (payload?.[0]?.payload as (typeof items)[number] | undefined) : undefined;
              if (!p) return null;
              return (
                <TooltipCard
                  title={p.label}
                  rows={[
                    { key: "v", label: "Spend", value: usd(p.value), color: p.color },
                    { key: "s", label: "Share", value: fmtShare(total ? p.value / total : 0) },
                  ]}
                />
              );
            }}
          />
        </PieChart>
      </ResponsiveContainer>
      <div className="pointer-events-none absolute inset-0 flex flex-col items-center justify-center">
        <span className="text-[11px] uppercase tracking-wide text-subtle">{centerLabel}</span>
        <span className="tnum text-xl font-semibold text-fg">{centerValue}</span>
      </div>
    </div>
  );
}

/* ---------------- Sparkline (KPI tiles) ---------------- */

export function Sparkline({ values, color = "var(--accent)", height = 36 }: { values: number[]; color?: string; height?: number }) {
  const data = values.map((v, i) => ({ i, v }));
  if (data.length < 2) return <div style={{ height }} />;
  return (
    <div style={{ height }} aria-hidden>
      <ResponsiveContainer width="100%" height="100%">
        <AreaChart data={data} margin={{ top: 2, right: 0, bottom: 0, left: 0 }}>
          <defs>
            <linearGradient id={`spark-${color.replace(/[^a-z0-9]/gi, "")}`} x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor={color} stopOpacity={0.3} />
              <stop offset="100%" stopColor={color} stopOpacity={0} />
            </linearGradient>
          </defs>
          <Area
            type="monotone"
            dataKey="v"
            stroke={color}
            strokeWidth={1.5}
            fill={`url(#spark-${color.replace(/[^a-z0-9]/gi, "")})`}
            isAnimationActive={false}
            dot={false}
          />
        </AreaChart>
      </ResponsiveContainer>
    </div>
  );
}
