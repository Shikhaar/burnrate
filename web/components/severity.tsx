import { AlertOctagon, AlertTriangle, Info } from "lucide-react";
import type { BudgetStatus, Severity } from "@/lib/api";
import { Badge } from "./ui";

export function SeverityBadge({ severity }: { severity: Severity | string }) {
  if (severity === "high")
    return (
      <Badge tone="bad" icon={<AlertOctagon className="size-3" aria-hidden />}>
        High
      </Badge>
    );
  if (severity === "medium")
    return (
      <Badge tone="warn" icon={<AlertTriangle className="size-3" aria-hidden />}>
        Medium
      </Badge>
    );
  return (
    <Badge tone="neutral" icon={<Info className="size-3" aria-hidden />}>
      Low
    </Badge>
  );
}

export const budgetTone = (s: BudgetStatus | string) => (s === "exceeded" ? "bad" : s === "warning" ? "warn" : "ok") as "bad" | "warn" | "ok";
export const budgetColor = (s: BudgetStatus | string) => (s === "exceeded" ? "var(--bad)" : s === "warning" ? "var(--warn)" : "var(--ok)");
export const budgetLabel = (s: BudgetStatus | string) => (s === "exceeded" ? "Exceeded" : s === "warning" ? "At risk" : "On track");

export function BudgetStatusBadge({ status }: { status: BudgetStatus | string }) {
  return <Badge tone={budgetTone(status)}>{budgetLabel(status)}</Badge>;
}
