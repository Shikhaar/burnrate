"use client";

import clsx from "clsx";
import {
  BarChart3,
  Compass,
  Flame,
  Lightbulb,
  ListOrdered,
  Menu,
  Moon,
  Scale,
  Sun,
  Tags,
  TrendingUp,
  Wallet,
  X,
} from "lucide-react";
import Link from "next/link";
import { usePathname, useSearchParams } from "next/navigation";
import { useEffect, useState, useSyncExternalStore, type ReactNode } from "react";
import { API_URL } from "@/lib/api";
import { RANGE_OPTIONS, useDays } from "@/lib/use-days";
import { Segmented } from "./ui";

const NAV = [
  { href: "/", label: "Overview", icon: BarChart3 },
  { href: "/explore", label: "Explore", icon: Compass },
  { href: "/requests", label: "Requests", icon: ListOrdered },
  { href: "/budgets", label: "Budgets", icon: Wallet },
  { href: "/forecast", label: "Anomalies & Forecast", icon: TrendingUp },
  { href: "/reconciliation", label: "Reconciliation", icon: Scale },
  { href: "/insights", label: "Insights", icon: Lightbulb },
  { href: "/pricing", label: "Pricing", icon: Tags },
] as const;

/** Pages whose data does not depend on the reporting window. */
const RANGELESS = new Set(["/pricing", "/insights", "/budgets", "/requests"]);

export function Logo({ className }: { className?: string }) {
  return (
    <span className={clsx("flex items-center gap-2", className)}>
      <span className="grid size-7 place-items-center rounded-lg bg-gradient-to-br from-amber-400 via-orange-500 to-red-500 text-white shadow-sm shadow-orange-500/30">
        <Flame className="size-4" strokeWidth={2.5} aria-hidden />
      </span>
      <span className="text-[15px] font-semibold tracking-tight text-fg">Burnrate</span>
    </span>
  );
}

function NavLinks({ onNavigate }: { onNavigate?: () => void }) {
  const pathname = usePathname();
  const params = useSearchParams();
  const days = params.get("days");
  return (
    <nav className="flex flex-col gap-0.5" aria-label="Main">
      {NAV.map(({ href, label, icon: Icon }) => {
        const active = href === "/" ? pathname === "/" : pathname.startsWith(href);
        const to = days ? `${href}?days=${days}` : href;
        return (
          <Link
            key={href}
            href={to}
            onClick={onNavigate}
            aria-current={active ? "page" : undefined}
            className={clsx(
              "group flex items-center gap-2.5 rounded-lg px-2.5 py-1.5 text-[13px] font-medium transition-colors",
              active ? "bg-surface-2 text-fg" : "text-muted hover:bg-surface-2/60 hover:text-fg",
            )}
          >
            <Icon className={clsx("size-4 shrink-0", active ? "text-accent" : "text-subtle group-hover:text-fg-2")} aria-hidden />
            <span className="truncate">{label}</span>
          </Link>
        );
      })}
    </nav>
  );
}

function SidebarFooter() {
  let host = API_URL;
  try {
    host = new URL(API_URL).host;
  } catch {
    /* keep raw */
  }
  return (
    <div className="mt-auto space-y-3 border-t border-border pt-3">
      <div className="rounded-lg border border-border bg-surface-2/50 p-3">
        <p className="text-[11px] font-medium uppercase tracking-wide text-subtle">API</p>
        <p className="mt-0.5 truncate font-mono text-[12px] text-fg-2" title={API_URL}>
          {host}
        </p>
      </div>
      <p className="px-1 text-[11px] text-subtle">AI spend intelligence</p>
    </div>
  );
}

/* ---------------- Theme ---------------- */

type Theme = "light" | "dark";

function subscribeTheme(cb: () => void) {
  const obs = new MutationObserver(cb);
  obs.observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });
  return () => obs.disconnect();
}
const getTheme = (): Theme => (document.documentElement.dataset.theme === "light" ? "light" : "dark");

export function useTheme(): Theme {
  return useSyncExternalStore(subscribeTheme, getTheme, () => "dark");
}

function ThemeToggle() {
  const theme = useTheme();
  const toggle = () => {
    const next: Theme = theme === "dark" ? "light" : "dark";
    document.documentElement.dataset.theme = next;
    try {
      localStorage.setItem("burnrate-theme", next);
    } catch {
      /* storage blocked */
    }
  };
  return (
    <button
      type="button"
      onClick={toggle}
      aria-label={`Switch to ${theme === "dark" ? "light" : "dark"} mode`}
      title={`Switch to ${theme === "dark" ? "light" : "dark"} mode`}
      className="grid size-8 place-items-center rounded-lg border border-border bg-surface text-muted transition-colors hover:bg-surface-2 hover:text-fg"
    >
      {theme === "dark" ? <Sun className="size-4" aria-hidden /> : <Moon className="size-4" aria-hidden />}
    </button>
  );
}

function RangePicker() {
  const [days, setDays] = useDays();
  const pathname = usePathname();
  const disabled = RANGELESS.has(pathname);
  if (disabled) return null;
  return (
    <Segmented
      ariaLabel="Date range"
      value={days}
      onChange={setDays}
      options={RANGE_OPTIONS.map((d) => ({ value: d, label: `${d}d` }))}
    />
  );
}

function currentTitle(pathname: string) {
  const item = NAV.find((n) => (n.href === "/" ? pathname === "/" : pathname.startsWith(n.href)));
  return item?.label ?? "Burnrate";
}

export function AppShell({ children }: { children: ReactNode }) {
  const [open, setOpen] = useState(false);
  const pathname = usePathname();

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && setOpen(false);
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open]);

  return (
    <div className="min-h-dvh">
      {/* Desktop sidebar */}
      <aside className="fixed inset-y-0 left-0 z-30 hidden w-60 flex-col border-r border-border bg-surface px-3 py-4 lg:flex">
        <Link href="/" className="mb-6 px-1.5">
          <Logo />
        </Link>
        <NavLinks />
        <SidebarFooter />
      </aside>

      {/* Mobile drawer */}
      <div className={clsx("fixed inset-0 z-50 lg:hidden", open ? "pointer-events-auto" : "pointer-events-none")} aria-hidden={!open}>
        <div
          className={clsx("absolute inset-0 bg-black/50 backdrop-blur-[2px] transition-opacity", open ? "opacity-100" : "opacity-0")}
          onClick={() => setOpen(false)}
        />
        <aside
          className={clsx(
            "absolute inset-y-0 left-0 flex w-72 max-w-[85vw] flex-col border-r border-border bg-surface px-3 py-4 transition-transform duration-200",
            open ? "translate-x-0" : "-translate-x-full",
          )}
        >
          <div className="mb-6 flex items-center justify-between px-1.5">
            <Logo />
            <button type="button" onClick={() => setOpen(false)} className="grid size-8 place-items-center rounded-lg text-muted hover:bg-surface-2" aria-label="Close menu">
              <X className="size-4" aria-hidden />
            </button>
          </div>
          <NavLinks onNavigate={() => setOpen(false)} />
          <SidebarFooter />
        </aside>
      </div>

      <div className="lg:pl-60">
        <header className="sticky top-0 z-20 flex h-14 items-center gap-3 border-b border-border bg-bg/80 px-4 backdrop-blur-md sm:px-6">
          <button
            type="button"
            onClick={() => setOpen(true)}
            className="grid size-8 place-items-center rounded-lg border border-border bg-surface text-muted hover:text-fg lg:hidden"
            aria-label="Open menu"
          >
            <Menu className="size-4" aria-hidden />
          </button>
          <Link href="/" className="lg:hidden" aria-label="Burnrate home">
            <span className="grid size-7 place-items-center rounded-lg bg-gradient-to-br from-amber-400 via-orange-500 to-red-500 text-white">
              <Flame className="size-4" strokeWidth={2.5} aria-hidden />
            </span>
          </Link>
          <div className="hidden min-w-0 items-center gap-1.5 text-sm sm:flex">
            <span className="text-subtle">Burnrate</span>
            <span className="text-subtle">/</span>
            <span className="truncate font-medium text-fg">{currentTitle(pathname)}</span>
          </div>
          <div className="ml-auto flex items-center gap-2">
            <RangePicker />
            <ThemeToggle />
          </div>
        </header>
        <main className="mx-auto w-full max-w-[1400px] px-4 py-6 sm:px-6 lg:py-8">{children}</main>
      </div>
    </div>
  );
}
