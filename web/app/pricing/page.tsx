"use client";

import { ArrowDown, ArrowUp, Search } from "lucide-react";
import { useMemo, useState } from "react";
import { Card, CardBody, DataState, Dot, EmptyState, PageHeader, Segmented, Table, TableSkeleton, Td, Th } from "@/components/ui";
import { usePricing, type PriceEntry } from "@/lib/api";
import { providerColor, providerLabel, providerRank } from "@/lib/colors";
import { day, usdPrice } from "@/lib/format";

type SortKey = "model" | "input_per_mtok" | "output_per_mtok";

export default function PricingPage() {
  const q = usePricing();
  const [provider, setProvider] = useState("all");
  const [search, setSearch] = useState("");
  const [sort, setSort] = useState<{ key: SortKey; dir: 1 | -1 }>({ key: "model", dir: 1 });

  const providers = useMemo(
    () => [...new Set((q.data?.items ?? []).map((p) => p.provider))].sort((a, b) => providerRank(a) - providerRank(b)),
    [q.data],
  );

  const rows = useMemo(() => {
    const s = search.trim().toLowerCase();
    return (q.data?.items ?? [])
      .filter((p) => (provider === "all" || p.provider === provider) && (!s || p.model.toLowerCase().includes(s)))
      .sort((a, b) => {
        if (sort.key === "model") {
          return (providerRank(a.provider) - providerRank(b.provider) || a.model.localeCompare(b.model)) * sort.dir;
        }
        return ((a[sort.key] ?? 0) - (b[sort.key] ?? 0)) * sort.dir;
      });
  }, [q.data, provider, search, sort]);

  const SortTh = ({ k, children, align }: { k: SortKey; children: string; align?: "right" }) => (
    <Th align={align}>
      <button
        type="button"
        className="inline-flex items-center gap-1 uppercase hover:text-fg"
        onClick={() => setSort((s) => ({ key: k, dir: s.key === k ? ((s.dir * -1) as 1 | -1) : k === "model" ? 1 : -1 }))}
      >
        {children}
        {sort.key === k ? sort.dir === 1 ? <ArrowUp className="size-3" aria-hidden /> : <ArrowDown className="size-3" aria-hidden /> : null}
      </button>
    </Th>
  );

  return (
    <>
      <PageHeader title="Pricing" description="Model price registry used to estimate cost from token counts. Prices are USD per million tokens." />
      <Card>
        <div className="flex flex-col gap-3 border-b border-border p-4 sm:flex-row sm:items-center sm:justify-between sm:px-5">
          {providers.length ? (
            <Segmented
              size="sm"
              ariaLabel="Provider"
              value={provider}
              onChange={setProvider}
              options={[{ value: "all", label: "All" }, ...providers.map((p) => ({ value: p, label: providerLabel(p) }))]}
            />
          ) : (
            <span />
          )}
          <label className="relative block sm:w-64">
            <Search className="pointer-events-none absolute left-2.5 top-1/2 size-3.5 -translate-y-1/2 text-subtle" aria-hidden />
            <input
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder="Search models"
              aria-label="Search models"
              className="h-8 w-full rounded-lg border border-border bg-surface pl-8 pr-2 text-sm text-fg outline-none placeholder:text-subtle focus:border-accent focus:ring-2 focus:ring-accent/20"
            />
          </label>
        </div>
        <CardBody className="pt-0">
          <DataState query={q} compact={false} loading={<div className="pt-4"><TableSkeleton rows={10} cols={6} /></div>} isEmpty={(d) => d.items.length === 0} empty={<EmptyState title="No prices registered" />}>
            {() =>
              rows.length === 0 ? (
                <EmptyState title="No models match" description="Try a different search or provider." />
              ) : (
                <Table>
                  <thead>
                    <tr>
                      <SortTh k="model">Model</SortTh>
                      <Th className="hidden sm:table-cell">Provider</Th>
                      <SortTh k="input_per_mtok" align="right">Input</SortTh>
                      <SortTh k="output_per_mtok" align="right">Output</SortTh>
                      <Th align="right" className="hidden md:table-cell">
                        Cached input
                      </Th>
                      <Th align="right" className="hidden md:table-cell">
                        Cache write
                      </Th>
                      <Th align="right" className="hidden lg:table-cell">
                        Output : input
                      </Th>
                      <Th align="right" className="hidden lg:table-cell">
                        Effective
                      </Th>
                    </tr>
                  </thead>
                  <tbody>
                    {rows.map((p: PriceEntry) => (
                      <tr key={`${p.provider}/${p.model}/${p.effective_from ?? ""}`} className="hover:bg-surface-2/50">
                        <Td>
                          <span className="flex items-center gap-2">
                            <Dot color={providerColor(p.provider)} />
                            <span className="font-mono text-[12px] text-fg">{p.model}</span>
                          </span>
                        </Td>
                        <Td className="hidden text-fg-2 sm:table-cell">{providerLabel(p.provider)}</Td>
                        <Td align="right" className="text-fg">
                          {usdPrice(p.input_per_mtok)}
                        </Td>
                        <Td align="right" className="text-fg">
                          {usdPrice(p.output_per_mtok)}
                        </Td>
                        <Td align="right" className="hidden text-fg-2 md:table-cell">
                          {usdPrice(p.cached_input_per_mtok)}
                        </Td>
                        <Td align="right" className="hidden text-fg-2 md:table-cell">
                          {usdPrice(p.cache_write_per_mtok)}
                        </Td>
                        <Td align="right" className="hidden text-muted lg:table-cell">
                          {p.input_per_mtok ? `${(p.output_per_mtok / p.input_per_mtok).toFixed(1)}×` : "—"}
                        </Td>
                        <Td align="right" className="hidden text-muted lg:table-cell">
                          {p.effective_from ? day(p.effective_from) : "—"}
                        </Td>
                      </tr>
                    ))}
                  </tbody>
                </Table>
              )
            }
          </DataState>
        </CardBody>
      </Card>
    </>
  );
}
