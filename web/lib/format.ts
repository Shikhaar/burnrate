const usd0 = new Intl.NumberFormat("en-US", { style: "currency", currency: "USD", maximumFractionDigits: 0 });
const usd2 = new Intl.NumberFormat("en-US", { style: "currency", currency: "USD", minimumFractionDigits: 2, maximumFractionDigits: 2 });
const int = new Intl.NumberFormat("en-US", { maximumFractionDigits: 0 });
const compact = new Intl.NumberFormat("en-US", { notation: "compact", maximumFractionDigits: 1 });
const compactUsd = new Intl.NumberFormat("en-US", { notation: "compact", maximumSignificantDigits: 3 });

const isNum = (n: unknown): n is number => typeof n === "number" && Number.isFinite(n);

/** $12.8k style, for KPIs and axes */
export function usdCompact(n: number | null | undefined): string {
  if (!isNum(n)) return "—";
  const abs = Math.abs(n);
  const sign = n < 0 ? "-" : "";
  if (abs === 0) return "$0";
  if (abs < 0.01) return `${sign}<$0.01`;
  if (abs < 10) return usd2.format(n);
  // Decide on compact notation after rounding, so 999.6 reads "$1k" like 1000 does.
  if (Math.round(abs) < 1000) return usd0.format(n);
  return `${sign}$${compactUsd.format(abs).toLowerCase()}`;
}

/** Exact currency for tables; sub-cent values get more precision. */
export function usd(n: number | null | undefined): string {
  if (!isNum(n)) return "—";
  if (n !== 0 && Math.abs(n) < 0.01) {
    return `${n < 0 ? "-" : ""}$${Math.abs(n).toFixed(Math.abs(n) < 0.0001 ? 6 : 4)}`;
  }
  return usd2.format(n);
}

/** Per-request cost: fixed 4 decimals so a column of small values lines up. */
export function usdFine(n: number | null | undefined): string {
  if (!isNum(n)) return "—";
  if (Math.abs(n) >= 100) return usd2.format(n);
  const sign = n < 0 ? "-" : "";
  if (n !== 0 && Math.abs(n) < 0.0001) return `${sign}$${Math.abs(n).toFixed(6)}`;
  return `${sign}$${Math.abs(n).toFixed(4)}`;
}

/** Price per million tokens, e.g. $0.075 or $15.00 */
export function usdPrice(n: number | null | undefined): string {
  if (!isNum(n)) return "—";
  if (n !== 0 && Math.abs(n) < 1) return `${n < 0 ? "-" : ""}$${Math.abs(n).toFixed(3).replace(/0$/, "")}`;
  return usd2.format(n);
}

export function num(n: number | null | undefined): string {
  return isNum(n) ? int.format(n) : "—";
}

export function numCompact(n: number | null | undefined): string {
  return isNum(n) ? compact.format(n) : "—";
}

/** Fraction 0..1 → "42.1%" */
export function share(n: number | null | undefined, digits = 1): string {
  return isNum(n) ? `${(n * 100).toFixed(digits)}%` : "—";
}

/**
 * Percent-point change, e.g. 12.5 → "+12.5%".
 * `change_pct` / `delta_pct` in the contract are interpreted as percent points (unlike `share`, which is 0..1).
 */
export function pct(n: number | null | undefined, digits = 1): string {
  if (!isNum(n)) return "—";
  const fixed = n.toFixed(digits);
  if (Number(fixed) === 0) return `${(0).toFixed(digits)}%`; // no "-0.0%"
  return `${n > 0 ? "+" : ""}${fixed}%`;
}

export function ms(n: number | null | undefined): string {
  if (!isNum(n)) return "—";
  if (n >= 1000) {
    const secs = n / 1000;
    // Pick precision after rounding so 9960ms reads "10s", not "10.0s".
    return Number(secs.toFixed(1)) >= 10 ? `${Math.round(secs)}s` : `${secs.toFixed(1)}s`;
  }
  return `${Math.round(n)}ms`;
}

const dayFmt = new Intl.DateTimeFormat("en-US", { month: "short", day: "numeric", timeZone: "UTC" });
const dayLongFmt = new Intl.DateTimeFormat("en-US", { weekday: "short", month: "short", day: "numeric", year: "numeric", timeZone: "UTC" });
const tsFmt = new Intl.DateTimeFormat("en-US", { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit", second: "2-digit", hourCycle: "h23" });
const monthFmt = new Intl.DateTimeFormat("en-US", { month: "long", year: "numeric", timeZone: "UTC" });

function parseDay(d: string): Date {
  return /^\d{4}-\d{2}-\d{2}$/.test(d) ? new Date(`${d}T00:00:00Z`) : new Date(d);
}

export const day = (d: string) => (d ? dayFmt.format(parseDay(d)) : "—");
export const dayLong = (d: string) => (d ? dayLongFmt.format(parseDay(d)) : "—");
export const timestamp = (d: string) => {
  // Naive ISO timestamps (no offset) from the API are UTC.
  const t = new Date(/[zZ]|[+-]\d{2}:?\d{2}$/.test(d) ? d : `${d}Z`);
  return Number.isNaN(t.getTime()) ? d : tsFmt.format(t);
};
export function monthLabel(m: string): string {
  if (!m) return "—";
  const d = /^\d{4}-\d{2}$/.test(m) ? new Date(`${m}-01T00:00:00Z`) : parseDay(m);
  return Number.isNaN(d.getTime()) ? m : monthFmt.format(d);
}

/** Percent change between two totals, null when the base is 0/missing. */
export function changePct(current: number, previous: number | null | undefined): number | null {
  if (!isNum(previous) || previous === 0) return null;
  return ((current - previous) / previous) * 100;
}

export function titleCase(s: string): string {
  return s.replace(/[_-]+/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
}
