"use client";

import clsx from "clsx";
import { AlertTriangle, ArrowDownRight, ArrowUpRight, Inbox, Minus, RefreshCw, WifiOff } from "lucide-react";
import type { ReactNode } from "react";
import type { SWRResponse } from "swr";
import { API_URL, type ApiError } from "@/lib/api";
import { pct } from "@/lib/format";

/* ---------------- Layout primitives ---------------- */

export function PageHeader({ title, description, actions }: { title: string; description?: ReactNode; actions?: ReactNode }) {
  return (
    <div className="mb-6 flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between">
      <div className="min-w-0">
        <h1 className="text-xl font-semibold tracking-tight text-fg sm:text-2xl">{title}</h1>
        {description ? <p className="mt-1 text-sm text-muted">{description}</p> : null}
      </div>
      {actions ? <div className="flex shrink-0 flex-wrap items-center gap-2">{actions}</div> : null}
    </div>
  );
}

export function Card({ className, children, ...rest }: React.HTMLAttributes<HTMLDivElement>) {
  return (
    <div
      className={clsx("rounded-xl border border-border bg-surface shadow-[0_1px_0_0_rgb(0_0_0/0.02)]", className)}
      {...rest}
    >
      {children}
    </div>
  );
}

export function CardHeader({
  title,
  description,
  actions,
  className,
}: {
  title: ReactNode;
  description?: ReactNode;
  actions?: ReactNode;
  className?: string;
}) {
  return (
    <div className={clsx("flex items-start justify-between gap-3 px-4 pt-4 sm:px-5", className)}>
      <div className="min-w-0">
        <h2 className="text-sm font-medium text-fg">{title}</h2>
        {description ? <p className="mt-0.5 text-xs text-muted">{description}</p> : null}
      </div>
      {actions ? <div className="flex shrink-0 items-center gap-2">{actions}</div> : null}
    </div>
  );
}

export function CardBody({ className, children }: { className?: string; children: ReactNode }) {
  return <div className={clsx("px-4 pb-4 pt-3 sm:px-5", className)}>{children}</div>;
}

/* ---------------- Badges & deltas ---------------- */

type Tone = "neutral" | "ok" | "warn" | "bad" | "accent";

const toneClass: Record<Tone, string> = {
  neutral: "bg-surface-2 text-fg-2 ring-border",
  ok: "bg-ok-soft text-ok ring-ok/20",
  warn: "bg-warn-soft text-warn ring-warn/20",
  bad: "bg-bad-soft text-bad ring-bad/20",
  accent: "bg-accent-soft text-accent ring-accent/20",
};

export function Badge({ tone = "neutral", children, className, icon }: { tone?: Tone; children: ReactNode; className?: string; icon?: ReactNode }) {
  return (
    <span
      className={clsx(
        "inline-flex items-center gap-1 whitespace-nowrap rounded-md px-1.5 py-0.5 text-[11px] font-medium ring-1 ring-inset",
        toneClass[tone],
        className,
      )}
    >
      {icon}
      {children}
    </span>
  );
}

/**
 * Change indicator. For spend, up is bad (red) and down is good (green) by default.
 */
export function Delta({ value, invert = false, className, suffix }: { value: number | null | undefined; invert?: boolean; className?: string; suffix?: string }) {
  if (value === null || value === undefined || !Number.isFinite(value)) {
    return <span className={clsx("tnum text-xs text-subtle", className)}>new</span>;
  }
  const flat = Math.abs(value) < 0.05;
  const up = value > 0;
  const good = flat ? null : invert ? up : !up;
  const Icon = flat ? Minus : up ? ArrowUpRight : ArrowDownRight;
  return (
    <span
      className={clsx(
        "tnum inline-flex items-center gap-0.5 text-xs font-medium",
        good === null ? "text-muted" : good ? "text-ok" : "text-bad",
        className,
      )}
    >
      <Icon className="size-3.5" aria-hidden />
      {pct(value)}
      {suffix ? <span className="ml-1 font-normal text-muted">{suffix}</span> : null}
    </span>
  );
}

export function Dot({ color, className }: { color: string; className?: string }) {
  return <span className={clsx("inline-block size-2 shrink-0 rounded-[3px]", className)} style={{ background: color }} aria-hidden />;
}

/* ---------------- Controls ---------------- */

export function Segmented<T extends string | number>({
  value,
  options,
  onChange,
  size = "md",
  ariaLabel,
}: {
  value: T;
  options: readonly { value: T; label: ReactNode }[];
  onChange: (v: T) => void;
  size?: "sm" | "md";
  ariaLabel?: string;
}) {
  return (
    <div role="radiogroup" aria-label={ariaLabel} className="inline-flex max-w-full overflow-x-auto rounded-lg border border-border bg-surface-2 p-0.5">
      {options.map((o) => {
        const active = o.value === value;
        return (
          <button
            key={String(o.value)}
            type="button"
            role="radio"
            aria-checked={active}
            onClick={() => onChange(o.value)}
            className={clsx(
              "whitespace-nowrap rounded-md font-medium transition-colors focus-visible:outline-2 focus-visible:outline-accent",
              size === "sm" ? "px-2 py-0.5 text-xs" : "px-2.5 py-1 text-xs sm:text-[13px]",
              active ? "bg-surface text-fg shadow-sm ring-1 ring-border" : "text-muted hover:text-fg",
            )}
          >
            {o.label}
          </button>
        );
      })}
    </div>
  );
}

export function Select({
  label,
  value,
  onChange,
  options,
  placeholder = "All",
  className,
}: {
  label: string;
  value: string;
  onChange: (v: string) => void;
  options: { value: string; label: string }[];
  placeholder?: string;
  className?: string;
}) {
  return (
    <label className={clsx("flex min-w-0 flex-col gap-1", className)}>
      <span className="text-[11px] font-medium uppercase tracking-wide text-subtle">{label}</span>
      <select
        value={value}
        onChange={(e) => onChange(e.target.value)}
        className="h-8 min-w-0 rounded-lg border border-border bg-surface px-2 text-sm text-fg outline-none focus:border-accent focus:ring-2 focus:ring-accent/20"
      >
        <option value="">{placeholder}</option>
        {options.map((o) => (
          <option key={o.value} value={o.value}>
            {o.label}
          </option>
        ))}
      </select>
    </label>
  );
}

export function Button({
  variant = "secondary",
  size = "md",
  className,
  children,
  ...rest
}: React.ButtonHTMLAttributes<HTMLButtonElement> & { variant?: "primary" | "secondary" | "ghost" | "danger"; size?: "sm" | "md" }) {
  return (
    <button
      type="button"
      className={clsx(
        "inline-flex items-center justify-center gap-1.5 rounded-lg font-medium transition-colors disabled:cursor-not-allowed disabled:opacity-50 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent",
        size === "sm" ? "h-7 px-2 text-xs" : "h-8 px-3 text-sm",
        variant === "primary" && "bg-accent text-accent-fg hover:opacity-90",
        variant === "secondary" && "border border-border bg-surface text-fg hover:bg-surface-2",
        variant === "ghost" && "text-muted hover:bg-surface-2 hover:text-fg",
        variant === "danger" && "text-muted hover:bg-bad-soft hover:text-bad",
        className,
      )}
      {...rest}
    >
      {children}
    </button>
  );
}

/* ---------------- Data states ---------------- */

export function Skeleton({ className }: { className?: string }) {
  return <div className={clsx("skeleton rounded-md", className)} />;
}

export function ErrorState({ error, onRetry, compact = false }: { error: ApiError | Error; onRetry?: () => void; compact?: boolean }) {
  const unreachable = "status" in error && (error as ApiError).status === 0;
  const Icon = unreachable ? WifiOff : AlertTriangle;
  if (compact) {
    return (
      <div className="flex flex-col items-center justify-center gap-2 py-8 text-center">
        <Icon className="size-5 text-subtle" aria-hidden />
        <p className="text-xs text-muted">{unreachable ? "API unreachable — no data to show" : `Failed to load: ${error.message}`}</p>
        {onRetry ? (
          <Button size="sm" variant="ghost" onClick={onRetry}>
            <RefreshCw className="size-3.5" aria-hidden /> Retry
          </Button>
        ) : null}
      </div>
    );
  }
  return (
    <div className={clsx("flex flex-col items-center justify-center text-center", compact ? "gap-2 py-6" : "gap-3 py-12")}>
      <div className="grid size-10 place-items-center rounded-full bg-bad-soft text-bad">
        <Icon className="size-5" aria-hidden />
      </div>
      <div className="max-w-md px-4">
        <p className="text-sm font-medium text-fg">
          {unreachable ? (
            <>
              Can&apos;t reach the Burnrate API at <code className="font-mono text-[13px] text-accent">{API_URL}</code>
            </>
          ) : (
            "Something went wrong loading this data"
          )}
        </p>
        <p className="mt-1 text-xs text-muted">
          {unreachable
            ? "Make sure the backend is running (or set NEXT_PUBLIC_API_URL) and try again."
            : error.message}
        </p>
      </div>
      {onRetry ? (
        <Button size="sm" onClick={onRetry}>
          <RefreshCw className="size-3.5" aria-hidden /> Retry
        </Button>
      ) : null}
    </div>
  );
}

export function EmptyState({ title = "No data yet", description, icon, action }: { title?: string; description?: ReactNode; icon?: ReactNode; action?: ReactNode }) {
  return (
    <div className="flex flex-col items-center justify-center gap-2 py-10 text-center">
      <div className="grid size-10 place-items-center rounded-full bg-surface-2 text-subtle">{icon ?? <Inbox className="size-5" aria-hidden />}</div>
      <p className="text-sm font-medium text-fg">{title}</p>
      {description ? <p className="max-w-sm px-4 text-xs text-muted">{description}</p> : null}
      {action}
    </div>
  );
}

/**
 * Renders loading / error / empty / data branches for an SWR query.
 */
export function DataState<T>({
  query,
  loading,
  isEmpty,
  empty,
  compact,
  children,
}: {
  query: Pick<SWRResponse<T, ApiError>, "data" | "error" | "isLoading" | "mutate">;
  loading: ReactNode;
  isEmpty?: (data: T) => boolean;
  empty?: ReactNode;
  /** compact error (default). Pages without a page-level error banner pass false. */
  compact?: boolean;
  children: (data: T) => ReactNode;
}) {
  const { data, error, mutate } = query;
  if (error && !data) return <ErrorState error={error} compact={compact ?? true} onRetry={() => void mutate()} />;
  if (data === undefined) return <>{loading}</>;
  if (isEmpty?.(data)) return <>{empty ?? <EmptyState />}</>;
  return <>{children(data)}</>;
}

export function ChartSkeleton({ height = 260 }: { height?: number }) {
  return (
    <div className="flex items-end gap-1.5" style={{ height }} aria-busy aria-label="Loading chart">
      {Array.from({ length: 24 }).map((_, i) => (
        <div key={i} className="skeleton flex-1 rounded-sm" style={{ height: `${30 + ((i * 37) % 60)}%` }} />
      ))}
    </div>
  );
}

export function TableSkeleton({ rows = 6, cols = 4 }: { rows?: number; cols?: number }) {
  return (
    <div className="space-y-2" aria-busy aria-label="Loading table">
      {Array.from({ length: rows }).map((_, r) => (
        <div key={r} className="flex gap-3">
          {Array.from({ length: cols }).map((_, c) => (
            <Skeleton key={c} className={clsx("h-5", c === 0 ? "flex-[2]" : "flex-1")} />
          ))}
        </div>
      ))}
    </div>
  );
}

/* ---------------- Tables ---------------- */

export function Table({ children, className }: { children: ReactNode; className?: string }) {
  return (
    <div className={clsx("-mx-4 overflow-x-auto sm:-mx-5", className)}>
      <table className="w-full min-w-full border-collapse text-sm">{children}</table>
    </div>
  );
}

export function Th({ children, align = "left", className }: { children?: ReactNode; align?: "left" | "right" | "center"; className?: string }) {
  return (
    <th
      scope="col"
      className={clsx(
        "whitespace-nowrap border-b border-border px-3 py-2 text-[11px] font-medium uppercase tracking-wide text-subtle first:pl-4 last:pr-4 sm:first:pl-5 sm:last:pr-5",
        align === "right" ? "text-right" : align === "center" ? "text-center" : "text-left",
        className,
      )}
    >
      {children}
    </th>
  );
}

export function Td({ children, align = "left", className, mono }: { children?: ReactNode; align?: "left" | "right" | "center"; className?: string; mono?: boolean }) {
  return (
    <td
      className={clsx(
        "whitespace-nowrap border-b border-border/70 px-3 py-2 first:pl-4 last:pr-4 sm:first:pl-5 sm:last:pr-5",
        align === "right" ? "text-right" : align === "center" ? "text-center" : "text-left",
        (align === "right" || mono) && "tnum",
        mono && "font-mono text-[12px]",
        className,
      )}
    >
      {children}
    </td>
  );
}

/** Thin horizontal share bar used inside tables. */
export function ShareBar({ value, color = "var(--accent)" }: { value: number; color?: string }) {
  const w = Math.max(0, Math.min(1, value)) * 100;
  return (
    <div className="h-1.5 w-full min-w-12 overflow-hidden rounded-full bg-surface-2">
      <div className="h-full rounded-full" style={{ width: `${w}%`, background: color }} />
    </div>
  );
}
