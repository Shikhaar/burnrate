"use client";

import { useMemo } from "react";
import { RankedBars, TimeseriesArea } from "@/components/charts";
import {
  Card,
  CardBody,
  CardHeader,
  ChartSkeleton,
  DataState,
  Delta,
  Dot,
  EmptyState,
  PageHeader,
  Segmented,
  ShareBar,
  Table,
  TableSkeleton,
  Td,
  Th,
} from "@/components/ui";
import { DIMENSIONS, useBreakdown, useTimeseries, type Dimension } from "@/lib/api";
import { isOther, seriesColors, seriesLabel } from "@/lib/colors";
import { num, numCompact, share, titleCase, usd } from "@/lib/format";
import { useDays, useQueryState } from "@/lib/use-days";

const DIM_LABEL: Record<Dimension, string> = {
  provider: "Provider",
  model: "Model",
  project: "Project",
  feature: "Feature",
  team: "Team",
  environment: "Environment",
  customer: "Customer",
  user: "User",
};

export default function ExplorePage() {
  const [days] = useDays();
  const [dimRaw, setDim] = useQueryState("dim", "provider");
  const dim: Dimension = (DIMENSIONS as readonly string[]).includes(dimRaw) ? (dimRaw as Dimension) : "provider";

  const breakdown = useBreakdown(dim, days, 12);
  const ts = useTimeseries(dim, days);

  const items = useMemo(() => breakdown.data?.items ?? [], [breakdown.data]);
  const colors = useMemo(() => seriesColors(items.map((i) => i.key), dim), [items, dim]);

  return (
    <>
      <PageHeader title="Explore" description={`Slice the last ${days} days of spend by any attribution dimension.`} />

      <div className="mb-4">
        <Segmented
          ariaLabel="Dimension"
          value={dim}
          onChange={(v) => setDim(v)}
          options={DIMENSIONS.map((d) => ({ value: d, label: DIM_LABEL[d] }))}
        />
      </div>

      <div className="mb-4 grid grid-cols-1 gap-4 xl:grid-cols-5">
        <Card className="xl:col-span-2">
          <CardHeader title={`Spend by ${DIM_LABEL[dim].toLowerCase()}`} description="Ranked by total cost" />
          <CardBody>
            <DataState
              query={breakdown}
              loading={<ChartSkeleton height={300} />}
              isEmpty={(d) => d.items.length === 0}
              empty={<EmptyState title="Nothing to rank" description={`No ${DIM_LABEL[dim].toLowerCase()} attribution in this window.`} />}
            >
              {(d) => <RankedBars items={d.items} dimension={dim} />}
            </DataState>
          </CardBody>
        </Card>
        <Card className="xl:col-span-3">
          <CardHeader title={`Daily spend by ${DIM_LABEL[dim].toLowerCase()}`} description="Top series; click the legend to toggle" />
          <CardBody>
            <DataState
              query={ts}
              loading={<ChartSkeleton height={300} />}
              isEmpty={(d) => d.points.length === 0}
              empty={<EmptyState title="No timeseries data" />}
            >
              {(d) => <TimeseriesArea data={d} height={300} colorMap={breakdown.data ? colors : undefined} />}
            </DataState>
          </CardBody>
        </Card>
      </div>

      <Card>
        <CardHeader title="Breakdown" description={`${DIM_LABEL[dim]} · last ${days} days vs previous ${days} days`} />
        <CardBody>
          <DataState query={breakdown} compact={false} loading={<TableSkeleton rows={8} cols={6} />} isEmpty={(d) => d.items.length === 0} empty={<EmptyState />}>
            {(d) => {
              const total = d.items.reduce((s, i) => s + i.cost_usd, 0);
              const totalTokens = d.items.reduce((s, i) => s + i.tokens, 0);
              const totalReq = d.items.reduce((s, i) => s + i.requests, 0);
              return (
                <Table>
                  <thead>
                    <tr>
                      <Th className="w-8">#</Th>
                      <Th>{DIM_LABEL[dim]}</Th>
                      <Th align="right">Spend</Th>
                      <Th className="hidden w-40 md:table-cell">Share</Th>
                      <Th align="right">Change</Th>
                      <Th align="right">Tokens</Th>
                      <Th align="right">Requests</Th>
                      <Th align="right">Cost / 1k req</Th>
                    </tr>
                  </thead>
                  <tbody>
                    {d.items.map((it, i) => (
                      <tr key={it.key} className="hover:bg-surface-2/50">
                        <Td className="tnum text-subtle">{isOther(it.key) ? "" : i + 1}</Td>
                        <Td className={isOther(it.key) ? "text-muted" : "font-medium text-fg"}>
                          <span className="flex items-center gap-2">
                            <Dot color={colors[it.key]} />
                            <span className={dim === "model" && !isOther(it.key) ? "font-mono text-[12px]" : undefined}>
                              {dim === "environment" ? titleCase(it.key) : seriesLabel(it.key, dim)}
                            </span>
                          </span>
                        </Td>
                        <Td align="right" className="font-medium text-fg">
                          {usd(it.cost_usd)}
                        </Td>
                        <Td className="hidden md:table-cell">
                          <div className="flex items-center gap-2">
                            <ShareBar value={it.share} color={colors[it.key]} />
                            <span className="tnum w-12 text-right text-xs text-muted">{share(it.share)}</span>
                          </div>
                        </Td>
                        <Td align="right">{isOther(it.key) ? <span className="text-xs text-subtle">—</span> : <Delta value={it.change_pct} />}</Td>
                        <Td align="right" className="text-fg-2">
                          {numCompact(it.tokens)}
                        </Td>
                        <Td align="right" className="text-fg-2">
                          {num(it.requests)}
                        </Td>
                        <Td align="right" className="text-fg-2">
                          {it.requests ? usd((it.cost_usd / it.requests) * 1000) : "—"}
                        </Td>
                      </tr>
                    ))}
                  </tbody>
                  <tfoot>
                    <tr className="text-fg">
                      <Td />
                      <Td className="font-medium">Total</Td>
                      <Td align="right" className="font-semibold">
                        {usd(total)}
                      </Td>
                      <Td className="hidden md:table-cell" />
                      <Td />
                      <Td align="right">{numCompact(totalTokens)}</Td>
                      <Td align="right">{num(totalReq)}</Td>
                      <Td align="right">{totalReq ? usd((total / totalReq) * 1000) : "—"}</Td>
                    </tr>
                  </tfoot>
                </Table>
              );
            }}
          </DataState>
        </CardBody>
      </Card>
    </>
  );
}
