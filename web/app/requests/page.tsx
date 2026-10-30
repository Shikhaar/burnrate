"use client";

import { ChevronLeft, ChevronRight, FilterX } from "lucide-react";
import { Badge, Button, Card, CardBody, DataState, Dot, EmptyState, PageHeader, Select, Table, TableSkeleton, Td, Th } from "@/components/ui";
import { useEvents, useMeta, type UsageEvent } from "@/lib/api";
import { providerColor, providerLabel } from "@/lib/colors";
import { ms, num, timestamp, usdFine } from "@/lib/format";
import { useQueryPatch } from "@/lib/use-days";
import { useSearchParams } from "next/navigation";
import { useEffect, useState } from "react";

const PAGE_SIZE = 50;

function StatusBadge({ status }: { status: UsageEvent["status"] }) {
  const s = (status || "").toLowerCase();
  if (s === "success" || s === "ok") return <Badge tone="ok">{s}</Badge>;
  if (s === "error" || s === "failed") return <Badge tone="bad">{s}</Badge>;
  if (s === "timeout" || s === "rate_limited") return <Badge tone="warn">{s}</Badge>;
  return <Badge>{s || "unknown"}</Badge>;
}

function CustomerFilter({ value, onCommit }: { value: string; onCommit: (v: string) => void }) {
  const [draft, setDraft] = useState(value);
  // Follow external changes (Clear, back/forward) without remounting, so focus stays in the input.
  useEffect(() => setDraft(value), [value]);
  return (
    <label className="flex min-w-0 flex-col gap-1">
      <span className="text-[11px] font-medium uppercase tracking-wide text-subtle">Customer</span>
      <input
        value={draft}
        onChange={(e) => setDraft(e.target.value)}
        onBlur={() => draft.trim() !== value && onCommit(draft.trim())}
        onKeyDown={(e) => e.key === "Enter" && onCommit(draft.trim())}
        placeholder="customer_id"
        className="h-8 min-w-0 rounded-lg border border-border bg-surface px-2 font-mono text-[13px] text-fg outline-none placeholder:font-sans placeholder:text-subtle focus:border-accent focus:ring-2 focus:ring-accent/20"
      />
    </label>
  );
}

export default function RequestsPage() {
  const params = useSearchParams();
  const patch = useQueryPatch();
  const provider = params.get("provider") ?? "";
  const model = params.get("model") ?? "";
  const project = params.get("project") ?? "";
  const feature = params.get("feature") ?? "";
  const customer = params.get("customer") ?? "";
  const requestedPage = Math.max(0, Math.floor(Number(params.get("page") ?? 0)) || 0);

  const meta = useMeta();
  const events = useEvents({ limit: PAGE_SIZE, offset: requestedPage * PAGE_SIZE, provider, model, project, feature, customer });

  const total = events.data?.total ?? 0;
  const pages = Math.max(1, Math.ceil(total / PAGE_SIZE));
  const page = events.data ? Math.min(requestedPage, pages - 1) : requestedPage;
  // An out-of-range ?page= (stale link, filters narrowed) snaps back to the last real page.
  useEffect(() => {
    if (events.data && requestedPage > pages - 1) patch({ page: pages > 1 ? String(pages - 1) : null });
  }, [events.data, requestedPage, pages, patch]);
  const from = total ? page * PAGE_SIZE + 1 : 0;
  const to = Math.min(total, (page + 1) * PAGE_SIZE);
  const filtered = Boolean(provider || model || project || feature || customer);

  const setFilter = (k: string, v: string) => patch({ [k]: v || null, page: null });

  return (
    <>
      <PageHeader title="Requests" description="Every LLM call ingested by Burnrate, newest first." />

      <Card>
        <div className="flex flex-col gap-3 border-b border-border p-4 sm:flex-row sm:items-end sm:px-5">
          <div className="grid flex-1 grid-cols-1 gap-3 sm:grid-cols-3 xl:grid-cols-5">
            <Select
              label="Provider"
              value={provider}
              onChange={(v) => setFilter("provider", v)}
              options={(meta.data?.providers ?? []).map((p) => ({ value: p, label: providerLabel(p) }))}
              placeholder="All providers"
            />
            <Select
              label="Model"
              value={model}
              onChange={(v) => setFilter("model", v)}
              options={(meta.data?.models ?? []).map((m) => ({ value: m, label: m }))}
              placeholder="All models"
            />
            <Select
              label="Project"
              value={project}
              onChange={(v) => setFilter("project", v)}
              options={(meta.data?.projects ?? []).map((p) => ({ value: p, label: p }))}
              placeholder="All projects"
            />
            <Select
              label="Feature"
              value={feature}
              onChange={(v) => setFilter("feature", v)}
              options={(meta.data?.features ?? []).map((p) => ({ value: p, label: p }))}
              placeholder="All features"
            />
            <CustomerFilter value={customer} onCommit={(v) => setFilter("customer", v)} />
          </div>
          {filtered ? (
            <Button variant="ghost" onClick={() => patch({ provider: null, model: null, project: null, feature: null, customer: null, page: null })}>
              <FilterX className="size-4" aria-hidden /> Clear
            </Button>
          ) : null}
        </div>

        <CardBody className="pt-0">
          <DataState
            query={events}
            compact={false}
            loading={
              <div className="pt-4">
                <TableSkeleton rows={10} cols={8} />
              </div>
            }
            isEmpty={(d) => d.total === 0}
            empty={
              <EmptyState
                title={filtered ? "No requests match these filters" : "No requests yet"}
                description={filtered ? "Try clearing a filter." : "Instrument your app with the Burnrate SDK or POST to /v1/events."}
              />
            }
          >
            {(d) => (
              <div className={events.isValidating ? "opacity-60 transition-opacity" : "transition-opacity"}>
                <Table>
                  <thead>
                    <tr>
                      <Th>Time</Th>
                      <Th>Provider / model</Th>
                      <Th className="hidden md:table-cell">Project</Th>
                      <Th className="hidden lg:table-cell">Feature</Th>
                      <Th align="right">Input</Th>
                      <Th align="right">Output</Th>
                      <Th align="right" className="hidden xl:table-cell">
                        Cached
                      </Th>
                      <Th align="right" className="hidden xl:table-cell">
                        Reasoning
                      </Th>
                      <Th align="right">Cost</Th>
                      <Th align="right" className="hidden sm:table-cell">
                        Latency
                      </Th>
                      <Th>Status</Th>
                    </tr>
                  </thead>
                  <tbody>
                    {d.items.map((e) => (
                      <tr key={e.id} className="hover:bg-surface-2/50">
                        <Td className="tnum text-xs text-muted">{timestamp(e.timestamp)}</Td>
                        <Td>
                          <span className="flex items-center gap-2">
                            <Dot color={providerColor(e.provider)} />
                            <span className="font-mono text-[12px] text-fg">{e.model}</span>
                          </span>
                        </Td>
                        <Td className="hidden text-fg-2 md:table-cell">
                          {e.project ?? <span className="text-subtle">—</span>}
                          {e.environment ? <span className="ml-1.5 text-[11px] text-subtle">{e.environment}</span> : null}
                        </Td>
                        <Td className="hidden text-fg-2 lg:table-cell">{e.feature ?? <span className="text-subtle">—</span>}</Td>
                        <Td align="right" className="text-fg-2">
                          {num(e.input_tokens)}
                        </Td>
                        <Td align="right" className="text-fg-2">
                          {num(e.output_tokens)}
                        </Td>
                        <Td align="right" className="hidden text-muted xl:table-cell">
                          {e.cached_input_tokens ? num(e.cached_input_tokens) : "—"}
                        </Td>
                        <Td align="right" className="hidden text-muted xl:table-cell">
                          {e.reasoning_tokens ? num(e.reasoning_tokens) : "—"}
                        </Td>
                        <Td align="right" className="font-medium text-fg">
                          {e.pricing === "unpriced" ? (
                            <span className="text-amber-500" title="No registry price for this model; counted as $0">
                              unpriced
                            </span>
                          ) : (
                            <span title={e.pricing === "provider_reported" ? "Cost reported by the provider" : undefined}>
                              {usdFine(e.cost_usd)}
                              {e.pricing === "provider_reported" ? <sup className="ml-0.5 text-muted">*</sup> : null}
                            </span>
                          )}
                        </Td>
                        <Td align="right" className="hidden text-fg-2 sm:table-cell">
                          {ms(e.latency_ms)}
                        </Td>
                        <Td>
                          <StatusBadge status={e.status} />
                        </Td>
                      </tr>
                    ))}
                  </tbody>
                </Table>
              </div>
            )}
          </DataState>

          <div className="mt-4 flex items-center justify-between gap-3 text-xs text-muted">
            <span className="tnum">{total ? `${num(from)}–${num(to)} of ${num(total)}` : " "}</span>
            <div className="flex items-center gap-2">
              <span className="tnum hidden sm:inline">
                Page {page + 1} of {pages}
              </span>
              <Button size="sm" disabled={page === 0} onClick={() => patch({ page: page - 1 > 0 ? String(page - 1) : null })} aria-label="Previous page">
                <ChevronLeft className="size-3.5" aria-hidden /> Prev
              </Button>
              <Button size="sm" disabled={page + 1 >= pages} onClick={() => patch({ page: String(page + 1) })} aria-label="Next page">
                Next <ChevronRight className="size-3.5" aria-hidden />
              </Button>
            </div>
          </div>
        </CardBody>
      </Card>
    </>
  );
}
