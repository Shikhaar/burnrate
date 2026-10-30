"use client";

import clsx from "clsx";
import { Activity, BellRing, Plus, Trash2, Wallet, X } from "lucide-react";
import { useState, type FormEvent } from "react";
import { BudgetStatusBadge, budgetColor, SeverityBadge } from "@/components/severity";
import { Button, Card, CardBody, CardHeader, DataState, EmptyState, PageHeader, Skeleton, TableSkeleton } from "@/components/ui";
import { ApiError, createBudget, deleteBudget, DIMENSIONS, evaluateAlerts, useAlerts, useBudgets, useMeta, type Budget, type Dimension, type Meta } from "@/lib/api";
import { monthLabel, share, timestamp, usd, usdCompact } from "@/lib/format";

const META_KEY: Partial<Record<Dimension, keyof Meta>> = {
  provider: "providers",
  model: "models",
  project: "projects",
  feature: "features",
  team: "teams",
  environment: "environments",
};

export default function BudgetsPage() {
  const budgets = useBudgets();
  const [showForm, setShowForm] = useState(false);

  const items = budgets.data?.items ?? [];
  const totalLimit = items.filter((b) => !b.scope_dimension).reduce((s, b) => s + b.monthly_limit_usd, 0);
  const atRisk = items.filter((b) => b.status !== "ok").length;

  return (
    <>
      <PageHeader
        title="Budgets"
        description={`Monthly spend limits · ${monthLabel(new Date().toISOString().slice(0, 7))}${items.length ? ` · ${items.length} budgets${atRisk ? `, ${atRisk} need attention` : ""}` : ""}${totalLimit ? ` · ${usdCompact(totalLimit)} global limit` : ""}`}
        actions={
          <Button variant={showForm ? "secondary" : "primary"} onClick={() => setShowForm((v) => !v)}>
            {showForm ? <X className="size-4" aria-hidden /> : <Plus className="size-4" aria-hidden />}
            {showForm ? "Cancel" : "New budget"}
          </Button>
        }
      />

      {showForm ? (
        <BudgetForm
          onCreated={async () => {
            setShowForm(false);
            await budgets.mutate();
          }}
        />
      ) : null}

      <DataState
        query={budgets}
        compact={false}
        loading={
          <div className="grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-3">
            {Array.from({ length: 6 }).map((_, i) => (
              <Card key={i} className="p-5">
                <Skeleton className="h-5 w-32" />
                <Skeleton className="mt-4 h-8 w-40" />
                <Skeleton className="mt-4 h-2 w-full" />
              </Card>
            ))}
          </div>
        }
        isEmpty={(d) => d.items.length === 0}
        empty={
          <Card>
            <EmptyState
              icon={<Wallet className="size-5" aria-hidden />}
              title="No budgets yet"
              description="Create a monthly budget for your whole org or scope it to a provider, project, team or feature."
              action={
                !showForm ? (
                  <Button variant="primary" className="mt-2" onClick={() => setShowForm(true)}>
                    <Plus className="size-4" aria-hidden /> New budget
                  </Button>
                ) : undefined
              }
            />
          </Card>
        }
      >
        {(d) => (
          <div className="grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-3">
            {[...d.items]
              .sort((a, b) => b.utilization - a.utilization)
              .map((b) => (
                <BudgetCard key={b.id} budget={b} onDeleted={() => void budgets.mutate()} />
              ))}
          </div>
        )}
      </DataState>

      <AlertHistory onEvaluated={() => void budgets.mutate()} />
    </>
  );
}

function AlertHistory({ onEvaluated }: { onEvaluated: () => void }) {
  const q = useAlerts(30);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  const evaluate = async () => {
    setBusy(true);
    setMsg(null);
    try {
      const r = await evaluateAlerts();
      setMsg(r.fired ? `${r.fired} new alert${r.fired === 1 ? "" : "s"} fired` : "No new alerts");
      await q.mutate();
      onEvaluated();
    } catch (e) {
      setMsg(e instanceof Error ? e.message : "Evaluation failed");
    } finally {
      setBusy(false);
    }
  };
  return (
    <Card className="mt-6">
      <CardHeader
        title="Alert history"
        description="Budget and anomaly alerts from the last 30 days"
        actions={
          <>
            {msg ? <span className="text-xs text-muted">{msg}</span> : null}
            <Button size="sm" onClick={evaluate} disabled={busy}>
              <BellRing className="size-3.5" aria-hidden /> {busy ? "Evaluating…" : "Evaluate now"}
            </Button>
          </>
        }
      />
      <CardBody>
        <DataState
          query={q}
          loading={<TableSkeleton rows={4} cols={4} />}
          isEmpty={(d) => d.items.length === 0}
          empty={<EmptyState icon={<BellRing className="size-5" aria-hidden />} title="No alerts fired" description="Alerts fire when a budget crosses its threshold or an anomaly is detected." />}
        >
          {(d) => (
            <ul className="divide-y divide-border">
              {d.items.map((a) => (
                <li key={a.id} className="flex items-start gap-3 py-2.5 first:pt-0 last:pb-0">
                  <span className="mt-0.5 grid size-7 shrink-0 place-items-center rounded-lg bg-surface-2 text-fg-2">
                    {a.kind === "budget" ? <Wallet className="size-3.5" aria-hidden /> : <Activity className="size-3.5" aria-hidden />}
                  </span>
                  <div className="min-w-0 flex-1">
                    <div className="flex flex-wrap items-center gap-2">
                      <p className="text-sm font-medium text-fg">{a.title}</p>
                      <AlertSeverity severity={a.severity} />
                    </div>
                    <p className="mt-0.5 text-xs text-muted">{a.detail}</p>
                  </div>
                  <div className="shrink-0 text-right">
                    <p className="tnum text-xs text-muted">{timestamp(a.created_at)}</p>
                    <p className="mt-0.5 text-[11px]">
                      {a.delivered ? (
                        <span className="text-ok">Delivered</span>
                      ) : a.delivery_error ? (
                        <span className="text-bad" title={a.delivery_error}>
                          Delivery failed
                        </span>
                      ) : (
                        <span className="text-subtle">Not delivered</span>
                      )}
                    </p>
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

function AlertSeverity({ severity }: { severity: string }) {
  if (["high", "critical", "exceeded"].includes(severity)) return <SeverityBadge severity="high" />;
  if (["medium", "warning"].includes(severity)) return <SeverityBadge severity="medium" />;
  return <SeverityBadge severity="low" />;
}

function BudgetCard({ budget: b, onDeleted }: { budget: Budget; onDeleted: () => void }) {
  const [confirm, setConfirm] = useState(false);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const scale = Math.max(b.monthly_limit_usd, b.projected_usd, b.spent_usd) * 1.05 || 1;
  const spentPct = (b.spent_usd / scale) * 100;
  const limitPct = (b.monthly_limit_usd / scale) * 100;
  const projPct = (b.projected_usd / scale) * 100;
  const threshPct = ((b.monthly_limit_usd * b.alert_threshold) / scale) * 100;
  const color = budgetColor(b.status);
  const projectedOver = b.projected_usd > b.monthly_limit_usd;

  const remove = async () => {
    setBusy(true);
    setErr(null);
    try {
      await deleteBudget(b.id);
      onDeleted();
    } catch (e) {
      setErr(e instanceof Error ? e.message : "Delete failed");
      setBusy(false);
      setConfirm(false);
    }
  };

  return (
    <Card className={clsx("flex flex-col p-5", b.status === "exceeded" && "border-bad/40")}>
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <h3 className="truncate text-sm font-semibold text-fg">{b.name}</h3>
          <p className="mt-0.5 truncate text-xs text-muted">
            {b.scope_dimension ? (
              <>
                {b.scope_dimension}: <span className="font-mono text-fg-2">{b.scope_value}</span>
              </>
            ) : (
              "All spend"
            )}
          </p>
        </div>
        <BudgetStatusBadge status={b.status} />
      </div>

      <div className="mt-4 flex items-baseline gap-1.5">
        <span className="tnum text-2xl font-semibold tracking-tight text-fg">{usd(b.spent_usd)}</span>
        <span className="tnum text-sm text-muted">/ {usd(b.monthly_limit_usd)}</span>
      </div>

      <div className="relative mt-4 h-6" role="img" aria-label={`${share(b.utilization, 0)} of limit used; projected ${usd(b.projected_usd)}`}>
        {/* track */}
        <div className="absolute inset-x-0 top-2 h-2 overflow-hidden rounded-full bg-surface-2">
          {/* projected (hatched) */}
          <div
            className="absolute inset-y-0 left-0 rounded-full opacity-35"
            style={{
              width: `${Math.min(100, projPct)}%`,
              background: `repeating-linear-gradient(135deg, ${color} 0 3px, transparent 3px 6px)`,
            }}
          />
          <div className="absolute inset-y-0 left-0 rounded-full" style={{ width: `${Math.min(100, spentPct)}%`, background: color }} />
        </div>
        {/* alert threshold tick */}
        <div className="absolute top-1 h-4 w-px bg-warn/70" style={{ left: `${threshPct}%` }} title={`Alert at ${share(b.alert_threshold, 0)}`} />
        {/* limit line */}
        <div className="absolute top-0 h-6 w-0.5 rounded bg-fg/70" style={{ left: `${limitPct}%` }} title="Limit" />
        {/* projected marker */}
        <div className="absolute -top-0.5 size-0 -translate-x-1/2 border-x-4 border-t-[5px] border-x-transparent" style={{ left: `${Math.min(100, projPct)}%`, borderTopColor: color }} title="Projected" />
      </div>

      <dl className="mt-3 grid grid-cols-3 gap-2 text-xs">
        <div>
          <dt className="text-subtle">Used</dt>
          <dd className="tnum font-medium text-fg">{share(b.utilization, 0)}</dd>
        </div>
        <div>
          <dt className="text-subtle">Projected</dt>
          <dd className={clsx("tnum font-medium", projectedOver ? "text-bad" : "text-fg")}>{usdCompact(b.projected_usd)}</dd>
        </div>
        <div>
          <dt className="text-subtle">Alert at</dt>
          <dd className="tnum font-medium text-fg">{share(b.alert_threshold, 0)}</dd>
        </div>
      </dl>

      {err ? <p className="mt-3 text-xs text-bad">{err}</p> : null}

      <div className="mt-4 flex items-center justify-end gap-2 border-t border-border pt-3">
        {confirm ? (
          <>
            <span className="mr-auto text-xs text-muted">Delete this budget?</span>
            <Button size="sm" variant="ghost" onClick={() => setConfirm(false)} disabled={busy}>
              Cancel
            </Button>
            <Button size="sm" className="bg-bad text-white hover:opacity-90" onClick={remove} disabled={busy}>
              {busy ? "Deleting…" : "Delete"}
            </Button>
          </>
        ) : (
          <Button size="sm" variant="danger" onClick={() => setConfirm(true)} aria-label={`Delete budget ${b.name}`}>
            <Trash2 className="size-3.5" aria-hidden /> Delete
          </Button>
        )}
      </div>
    </Card>
  );
}

const inputCls =
  "h-9 w-full rounded-lg border border-border bg-surface px-3 text-sm text-fg outline-none placeholder:text-subtle focus:border-accent focus:ring-2 focus:ring-accent/20";

function Field({ label, hint, children }: { label: string; hint?: string; children: React.ReactNode }) {
  return (
    <label className="flex min-w-0 flex-col gap-1.5">
      <span className="text-xs font-medium text-fg-2">{label}</span>
      {children}
      {hint ? <span className="text-[11px] text-subtle">{hint}</span> : null}
    </label>
  );
}

function BudgetForm({ onCreated }: { onCreated: () => Promise<void> | void }) {
  const meta = useMeta();
  const [name, setName] = useState("");
  const [dim, setDim] = useState<Dimension | "">("");
  const [value, setValue] = useState("");
  const [limit, setLimit] = useState("");
  const [threshold, setThreshold] = useState("80");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const suggestions = dim && META_KEY[dim] && meta.data ? (meta.data[META_KEY[dim]!] ?? []) : [];

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setError(null);
    const limitNum = Number(limit);
    const thr = Number(threshold) / 100;
    if (!name.trim()) return setError("Give the budget a name.");
    if (!Number.isFinite(limitNum) || limitNum <= 0) return setError("Monthly limit must be a positive number.");
    if (!Number.isFinite(thr) || thr <= 0 || thr > 1) return setError("Alert threshold must be between 1 and 100%.");
    if (dim && !value.trim()) return setError(`Pick a ${dim} to scope this budget to.`);
    setBusy(true);
    try {
      await createBudget({
        name: name.trim(),
        scope_dimension: dim || null,
        scope_value: dim ? value.trim() : null,
        monthly_limit_usd: limitNum,
        alert_threshold: thr,
      });
      await onCreated();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not create budget.");
    } finally {
      setBusy(false);
    }
  };

  return (
    <Card className="mb-6 p-5">
      <form onSubmit={submit} className="space-y-4">
        <div>
          <h2 className="text-sm font-semibold text-fg">New budget</h2>
          <p className="text-xs text-muted">Budgets reset monthly. Leave scope empty to cap all AI spend.</p>
        </div>
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-5">
          <Field label="Name">
            <input className={inputCls} value={name} onChange={(e) => setName(e.target.value)} placeholder="e.g. Search team" autoFocus />
          </Field>
          <Field label="Scope">
            <select
              className={inputCls}
              value={dim}
              onChange={(e) => {
                setDim(e.target.value as Dimension | "");
                setValue("");
              }}
            >
              <option value="">All spend</option>
              {DIMENSIONS.map((d) => (
                <option key={d} value={d}>
                  {d[0].toUpperCase() + d.slice(1)}
                </option>
              ))}
            </select>
          </Field>
          <Field label="Scope value">
            <input
              className={inputCls}
              value={value}
              onChange={(e) => setValue(e.target.value)}
              placeholder={dim ? `Which ${dim}?` : "—"}
              disabled={!dim}
              list="budget-scope-values"
            />
            <datalist id="budget-scope-values">
              {suggestions.map((s) => (
                <option key={s} value={s} />
              ))}
            </datalist>
          </Field>
          <Field label="Monthly limit (USD)">
            <div className="relative">
              <span className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-sm text-subtle">$</span>
              <input className={clsx(inputCls, "tnum pl-6")} inputMode="decimal" value={limit} onChange={(e) => setLimit(e.target.value)} placeholder="5000" />
            </div>
          </Field>
          <Field label="Alert threshold">
            <div className="relative">
              <input
                className={clsx(inputCls, "tnum pr-7")}
                type="number"
                min={1}
                max={100}
                value={threshold}
                onChange={(e) => setThreshold(e.target.value)}
              />
              <span className="pointer-events-none absolute right-3 top-1/2 -translate-y-1/2 text-sm text-subtle">%</span>
            </div>
          </Field>
        </div>
        <div className="flex items-center justify-end gap-3">
          {error ? <p className="mr-auto text-xs text-bad">{error}</p> : null}
          <Button type="submit" variant="primary" disabled={busy}>
            {busy ? "Creating…" : "Create budget"}
          </Button>
        </div>
      </form>
    </Card>
  );
}
