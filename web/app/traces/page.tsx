"use client";

import clsx from "clsx";
import { ChevronLeft, ChevronRight, ExternalLink, Repeat2, Waypoints, X } from "lucide-react";
import { useSearchParams } from "next/navigation";
import { useEffect } from "react";
import { KpiTile } from "@/components/kpi";
import {
  Badge,
  Button,
  Card,
  CardBody,
  CardHeader,
  DataState,
  Dot,
  EmptyState,
  PageHeader,
  Segmented,
  Select,
  ShareBar,
  Table,
  TableSkeleton,
  Td,
  Th,
} from "@/components/ui";
import { useMeta, useTrace, useTraces, type ScoreMap, type TraceDetail, type TraceRow, type TracesQuery } from "@/lib/api";
import { providerColor } from "@/lib/colors";
import { ms, num, share, timestamp, usd, usdFine } from "@/lib/format";
import { useDays, useQueryPatch } from "@/lib/use-days";

const PAGE_SIZE = 25;
const SORTS: { value: TracesQuery["sort"]; label: string }[] = [
  { value: "cost", label: "Most expensive" },
  { value: "recent", label: "Newest" },
  { value: "calls", label: "Most calls" },
];

/** "triage ×4 → tagging → reply": consecutive repeats of a step collapse into one chip. */
function collapse(steps: string[]): { name: string; count: number }[] {
  const out: { name: string; count: number }[] = [];
  for (const s of steps) {
    const last = out[out.length - 1];
    if (last && last.name === s) last.count += 1;
    else out.push({ name: s, count: 1 });
  }
  return out;
}

function StepChain({ steps, calls }: { steps: string[]; calls: number }) {
  const groups = collapse(steps);
  return (
    <span className="flex max-w-[28rem] flex-wrap items-center gap-1 text-[12px] text-fg-2">
      {groups.map((g, i) => (
        <span key={i} className="flex items-center gap-1">
          {i > 0 ? <span className="text-subtle">→</span> : null}
          <span className={clsx("rounded px-1.5 py-0.5", g.count > 1 ? "bg-warn-soft text-warn" : "bg-surface-2")}>
            {g.name}
            {g.count > 1 ? <span className="tnum ml-1 font-medium">×{g.count}</span> : null}
          </span>
        </span>
      ))}
      {calls > steps.length ? <span className="text-subtle">+{calls - steps.length} more</span> : null}
    </span>
  );
}

function scoreText(v: number | string) {
  return typeof v === "number" ? v.toFixed(2) : v;
}

function Scores({ scores }: { scores: ScoreMap }) {
  const entries = Object.entries(scores);
  if (!entries.length) return <span className="text-subtle">—</span>;
  return (
    <span className="flex flex-wrap gap-1">
      {entries.map(([name, v]) => (
        <Badge key={name} tone={typeof v === "number" ? (v >= 0.7 ? "ok" : v < 0.4 ? "bad" : "warn") : "neutral"}>
          {name} {scoreText(v)}
        </Badge>
      ))}
    </span>
  );
}

function OpenLink({ href }: { href: string | null }) {
  if (!href) return null;
  return (
    <a
      href={href}
      target="_blank"
      rel="noreferrer noopener"
      className="inline-flex items-center gap-1 rounded-lg border border-border px-2.5 py-1 text-xs font-medium text-fg-2 hover:bg-surface-2 hover:text-fg"
    >
      Prompts and responses <ExternalLink className="size-3.5" aria-hidden />
    </a>
  );
}

function TracePanel({ id, onClose }: { id: string; onClose: () => void }) {
  const q = useTrace(id);
  return (
    <Card className="mb-4">
      <DataState query={q} loading={<div className="p-5"><TableSkeleton rows={5} cols={4} /></div>}>
        {(t: TraceDetail) => (
          <>
            <CardHeader
              title={t.name}
              description={
                <span className="flex flex-wrap items-center gap-x-2 gap-y-1">
                  <span className="font-mono text-[11px]">{t.trace_id}</span>
                  <span>·</span>
                  <span>{timestamp(t.started_at)}</span>
                  {t.customer_id ? <><span>·</span><span>customer {t.customer_id}</span></> : null}
                  {t.user_id ? <><span>·</span><span>user {t.user_id}</span></> : null}
                </span>
              }
              actions={
                <div className="flex items-center gap-2">
                  <OpenLink href={t.link} />
                  <Button size="sm" variant="ghost" onClick={onClose} aria-label="Close trace">
                    <X className="size-4" aria-hidden />
                  </Button>
                </div>
              }
            />
            <CardBody className="space-y-4">
              <dl className="grid grid-cols-2 gap-3 text-sm sm:grid-cols-5">
                <div><dt className="text-xs text-muted">Cost</dt><dd className="tnum font-semibold text-fg">{usdFine(t.cost_usd)}</dd></div>
                <div><dt className="text-xs text-muted">Duration</dt><dd className="tnum font-semibold text-fg">{ms(t.duration_ms)}</dd></div>
                <div><dt className="text-xs text-muted">Calls</dt><dd className="tnum font-semibold text-fg">{t.calls}</dd></div>
                <div>
                  <dt className="text-xs text-muted">Repeated steps</dt>
                  <dd className={clsx("tnum font-semibold", t.repeats ? "text-warn" : "text-fg")}>{t.repeats}</dd>
                </div>
                <div>
                  <dt className="text-xs text-muted">Errors</dt>
                  <dd className={clsx("tnum font-semibold", t.errors ? "text-bad" : "text-fg")}>{t.errors}</dd>
                </div>
              </dl>
              {t.repeats ? (
                <p role="note" className="flex items-start gap-2 rounded-lg bg-warn-soft px-3 py-2 text-sm text-warn">
                  <Repeat2 className="mt-0.5 size-4 shrink-0" aria-hidden />
                  {t.repeats} step{t.repeats === 1 ? "" : "s"} ran again right after the same step on the same model: a retry, or a loop
                  worth checking.
                </p>
              ) : null}

              <div>
                <p className="mb-2 text-xs font-medium text-fg-2">Steps</p>
                <ol className="space-y-1.5">
                  {t.steps.map((s, i) => {
                    const total = Math.max(t.duration_ms, 1);
                    const left = (s.offset_ms / total) * 100;
                    const width = Math.max(((s.latency_ms ?? 0) / total) * 100, 0.8);
                    const bad = s.status !== "success";
                    return (
                      <li key={s.id} className="grid grid-cols-[minmax(0,11rem)_minmax(0,1fr)_5.5rem] items-center gap-3 text-[12px] sm:grid-cols-[minmax(0,15rem)_minmax(0,1fr)_7rem]">
                        <span className="min-w-0">
                          <span className="flex items-center gap-1.5 truncate text-fg">
                            <span className="tnum text-subtle">{i + 1}</span>
                            <span className="truncate">{s.feature ?? s.operation}</span>
                            {s.repeat ? <Repeat2 className="size-3.5 shrink-0 text-warn" aria-label="Repeated step" /> : null}
                          </span>
                          <span className="flex items-center gap-1.5 truncate text-[11px] text-muted">
                            <Dot color={providerColor(s.provider)} />
                            <span className="truncate font-mono">{s.model}</span>
                          </span>
                        </span>
                        <span className="relative h-5 rounded bg-surface-2/60" title={`starts at +${ms(s.offset_ms)}, takes ${ms(s.latency_ms)}`}>
                          <span
                            className={clsx("absolute inset-y-1 rounded", bad ? "bg-bad" : s.repeat ? "bg-warn" : "bg-accent")}
                            style={{ left: `${Math.min(left, 99)}%`, width: `${Math.min(width, 100 - Math.min(left, 99))}%` }}
                          />
                        </span>
                        <span className="text-right">
                          <span className="tnum block font-medium text-fg">{usdFine(s.cost_usd)}</span>
                          <span className="mt-0.5 flex items-center justify-end gap-1.5">
                            <span className="w-10"><ShareBar value={s.cost_share} /></span>
                            <span className="tnum text-[11px] text-muted">{share(s.cost_share, 0)}</span>
                          </span>
                        </span>
                      </li>
                    );
                  })}
                </ol>
              </div>

              <div>
                <p className="mb-2 text-xs font-medium text-fg-2">Scores</p>
                {t.scores.length ? (
                  <ul className="flex flex-wrap gap-1.5">
                    {t.scores.map((s, i) => (
                      <li key={i}>
                        <Badge tone={s.value === null ? "neutral" : s.value >= 0.7 ? "ok" : s.value < 0.4 ? "bad" : "warn"}>
                          {s.name}: {s.label ?? (s.data_type === "BOOLEAN" ? (s.value ? "yes" : "no") : s.value?.toFixed(2))}
                          <span className="ml-1 opacity-70">{s.source}</span>
                        </Badge>
                      </li>
                    ))}
                  </ul>
                ) : (
                  <p className="text-xs text-muted">No scores for this trace. Send them to POST /v1/scores, or import them from Langfuse.</p>
                )}
              </div>
            </CardBody>
          </>
        )}
      </DataState>
    </Card>
  );
}

export default function TracesPage() {
  const params = useSearchParams();
  const patch = useQueryPatch();
  const [days] = useDays();
  const sort = (SORTS.find((s) => s.value === params.get("sort"))?.value ?? "cost") as TracesQuery["sort"];
  const project = params.get("project") ?? "";
  const errors = params.get("errors") === "1";
  const selected = params.get("trace");
  const requestedPage = Math.max(0, Math.floor(Number(params.get("page") ?? 0)) || 0);

  const meta = useMeta();
  const q = useTraces({ days, limit: PAGE_SIZE, offset: requestedPage * PAGE_SIZE, sort, project, errors });
  const total = q.data?.total ?? 0;
  const pages = Math.max(1, Math.ceil(total / PAGE_SIZE));
  const page = q.data ? Math.min(requestedPage, pages - 1) : requestedPage;
  useEffect(() => {
    if (q.data && requestedPage > pages - 1) patch({ page: pages > 1 ? String(pages - 1) : null });
  }, [q.data, requestedPage, pages, patch]);
  const s = q.data?.summary;

  return (
    <>
      <PageHeader
        title="Traces"
        description="Each agent run or request chain: its steps in order, what each one cost, and how good the result was."
      />

      <div className="mb-4 grid grid-cols-2 gap-3 lg:grid-cols-4">
        <KpiTile label="Traces" value={num(s?.traces)} sub={`last ${days} days`} icon={<Waypoints className="size-4" aria-hidden />} loading={!q.data} />
        <KpiTile label="Average cost per trace" value={usdFine(s?.avg_cost_usd)} loading={!q.data} />
        <KpiTile label="Calls per trace" value={s ? s.avg_calls.toFixed(1) : "—"} loading={!q.data} />
        <KpiTile label="Spend in traces" value={usd(s?.cost_usd)} loading={!q.data} />
      </div>

      {selected ? <TracePanel id={selected} onClose={() => patch({ trace: null })} /> : null}

      <Card className="mb-4">
        <CardHeader title="By workflow" description="Cost per run next to quality: a workflow that got pricier without getting better is worth a look." />
        <CardBody className="pt-0">
          <DataState query={q} loading={<TableSkeleton rows={3} cols={6} />} isEmpty={(d) => d.workflows.length === 0}
            empty={<EmptyState title="No traces yet" description="Calls are grouped into traces by trace_id: set it with the SDK's attribution context, send OpenTelemetry spans, or import from Langfuse." />}>
            {(d) => (
              <Table>
                <thead>
                  <tr>
                    <Th>Workflow</Th>
                    <Th align="right">Traces</Th>
                    <Th align="right">Avg cost</Th>
                    <Th align="right" className="hidden sm:table-cell">Calls per run</Th>
                    <Th align="right" className="hidden md:table-cell">Failed</Th>
                    <Th>Scores (average)</Th>
                    <Th align="right">Spend</Th>
                  </tr>
                </thead>
                <tbody>
                  {d.workflows.map((w) => (
                    <tr key={w.name}>
                      <Td className="font-medium text-fg">{w.name}</Td>
                      <Td align="right" className="text-fg-2">{num(w.traces)}</Td>
                      <Td align="right" className="font-medium text-fg">{usdFine(w.avg_cost_usd)}</Td>
                      <Td align="right" className="hidden text-fg-2 sm:table-cell">{w.avg_calls.toFixed(1)}</Td>
                      <Td align="right" className={clsx("hidden md:table-cell", w.failed_share > 0.05 ? "text-bad" : "text-fg-2")}>
                        {share(w.failed_share, 1)}
                      </Td>
                      <Td><Scores scores={w.scores} /></Td>
                      <Td align="right" className="text-fg-2">{usd(w.cost_usd)}</Td>
                    </tr>
                  ))}
                </tbody>
              </Table>
            )}
          </DataState>
        </CardBody>
      </Card>

      <Card>
        <div className="flex flex-col gap-3 border-b border-border p-4 sm:flex-row sm:items-end sm:justify-between sm:px-5">
          <div className="flex flex-wrap items-end gap-3">
            <Segmented ariaLabel="Sort traces" value={sort} onChange={(v) => patch({ sort: v === "cost" ? null : v, page: null })} options={SORTS} />
            <div className="w-48">
              <Select
                label="Project"
                value={project}
                onChange={(v) => patch({ project: v || null, page: null })}
                options={(meta.data?.projects ?? []).map((p) => ({ value: p, label: p }))}
                placeholder="All projects"
              />
            </div>
          </div>
          <label className="flex items-center gap-2 text-sm text-fg-2">
            <input type="checkbox" checked={errors} onChange={(e) => patch({ errors: e.target.checked ? "1" : null, page: null })} />
            Only traces with errors
          </label>
        </div>
        <CardBody className="pt-0">
          <DataState query={q} compact={false} loading={<div className="pt-4"><TableSkeleton rows={8} cols={7} /></div>}
            isEmpty={(d) => d.total === 0}
            empty={<EmptyState title={project || errors ? "No traces match" : "No traces yet"} description={project || errors ? "Try clearing a filter." : "Calls with a trace_id show up here."} />}>
            {(d) => (
              <div className={q.isValidating ? "opacity-60 transition-opacity" : "transition-opacity"}>
                <Table>
                  <thead>
                    <tr>
                      <Th>Started</Th>
                      <Th>Workflow</Th>
                      <Th className="hidden lg:table-cell">Steps</Th>
                      <Th align="right">Calls</Th>
                      <Th align="right" className="hidden sm:table-cell">Duration</Th>
                      <Th align="right">Cost</Th>
                      <Th className="hidden md:table-cell">Scores</Th>
                    </tr>
                  </thead>
                  <tbody>
                    {d.items.map((t: TraceRow) => (
                      <tr
                        key={t.trace_id}
                        className={clsx("cursor-pointer hover:bg-surface-2/50", selected === t.trace_id && "bg-accent-soft/40")}
                        onClick={() => patch({ trace: t.trace_id })}
                      >
                        <Td className="tnum text-xs text-muted">{timestamp(t.started_at)}</Td>
                        <Td>
                          <button
                            type="button"
                            className="text-left font-medium text-fg hover:text-accent"
                            onClick={(e) => {
                              e.stopPropagation();
                              patch({ trace: t.trace_id });
                            }}
                          >
                            {t.name}
                          </button>
                          <span className="block text-[11px] text-muted">
                            {t.customer_id ?? t.user_id ?? t.project}
                            {t.errors ? <span className="ml-1.5 text-bad">{t.errors} error{t.errors === 1 ? "" : "s"}</span> : null}
                          </span>
                        </Td>
                        <Td className="hidden lg:table-cell"><StepChain steps={t.steps} calls={t.calls} /></Td>
                        <Td align="right" className="text-fg-2">{t.calls}</Td>
                        <Td align="right" className="hidden text-fg-2 sm:table-cell">{ms(t.duration_ms)}</Td>
                        <Td align="right" className="font-medium text-fg">{usdFine(t.cost_usd)}</Td>
                        <Td className="hidden md:table-cell"><Scores scores={t.scores} /></Td>
                      </tr>
                    ))}
                  </tbody>
                </Table>
              </div>
            )}
          </DataState>
          <div className="mt-4 flex items-center justify-between gap-3 text-xs text-muted">
            <span className="tnum">{total ? `${num(page * PAGE_SIZE + 1)}–${num(Math.min(total, (page + 1) * PAGE_SIZE))} of ${num(total)}` : " "}</span>
            <div className="flex items-center gap-2">
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
