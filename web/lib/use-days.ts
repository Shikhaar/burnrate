"use client";

import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { useCallback } from "react";

export const RANGE_OPTIONS = [7, 30, 90] as const;
export const DEFAULT_DAYS = 30;

export function parseDays(v: string | null): number {
  const n = Number(v);
  return (RANGE_OPTIONS as readonly number[]).includes(n) ? n : DEFAULT_DAYS;
}

/** Reporting window, stored in the URL as `?days=` so it survives reloads and is shareable. */
export function useDays(): [number, (d: number) => void] {
  const params = useSearchParams();
  const router = useRouter();
  const pathname = usePathname();
  const days = parseDays(params.get("days"));
  const setDays = useCallback(
    (d: number) => {
      const next = new URLSearchParams(params.toString());
      if (d === DEFAULT_DAYS) next.delete("days");
      else next.set("days", String(d));
      const qs = next.toString();
      router.replace(qs ? `${pathname}?${qs}` : pathname, { scroll: false });
    },
    [params, router, pathname],
  );
  return [days, setDays];
}

/** Generic URL query state for page-level controls (dimension picker, filters, page). */
export function useQueryState(key: string, fallback = ""): [string, (v: string) => void] {
  const params = useSearchParams();
  const router = useRouter();
  const pathname = usePathname();
  const value = params.get(key) ?? fallback;
  const set = useCallback(
    (v: string) => {
      const next = new URLSearchParams(params.toString());
      if (!v || v === fallback) next.delete(key);
      else next.set(key, v);
      const qs = next.toString();
      router.replace(qs ? `${pathname}?${qs}` : pathname, { scroll: false });
    },
    [params, router, pathname, key, fallback],
  );
  return [value, set];
}

/** Patch several query params in one navigation (avoids racing replace() calls). */
export function useQueryPatch(): (patch: Record<string, string | null>) => void {
  const params = useSearchParams();
  const router = useRouter();
  const pathname = usePathname();
  return useCallback(
    (patch: Record<string, string | null>) => {
      const next = new URLSearchParams(params.toString());
      for (const [k, v] of Object.entries(patch)) {
        if (v === null || v === "") next.delete(k);
        else next.set(k, v);
      }
      const qs = next.toString();
      router.replace(qs ? `${pathname}?${qs}` : pathname, { scroll: false });
    },
    [params, router, pathname],
  );
}
