"use client";

import type { ReactNode } from "react";
import { Card, Skeleton } from "./ui";
import { Sparkline } from "./charts";

export function KpiTile({
  label,
  value,
  sub,
  icon,
  spark,
  sparkColor,
  loading,
}: {
  label: string;
  value: ReactNode;
  sub?: ReactNode;
  icon?: ReactNode;
  spark?: number[];
  sparkColor?: string;
  loading?: boolean;
}) {
  return (
    <Card className="relative overflow-hidden p-4">
      <div className="flex items-center justify-between">
        <span className="text-xs font-medium text-muted">{label}</span>
        {icon ? <span className="text-subtle">{icon}</span> : null}
      </div>
      {loading ? (
        <>
          <Skeleton className="mt-3 h-7 w-28" />
          <Skeleton className="mt-2 h-4 w-20" />
        </>
      ) : (
        <>
          <div className="tnum mt-2 text-2xl font-semibold tracking-tight text-fg">{value}</div>
          <div className="mt-1 min-h-4 text-xs text-muted">{sub}</div>
        </>
      )}
      {spark && !loading ? (
        <div className="-mx-4 -mb-4 mt-3">
          <Sparkline values={spark} color={sparkColor} />
        </div>
      ) : null}
    </Card>
  );
}
