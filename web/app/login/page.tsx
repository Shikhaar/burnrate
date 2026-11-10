"use client";

import { LogIn } from "lucide-react";
import { useRouter, useSearchParams } from "next/navigation";
import { useEffect, useState, type FormEvent } from "react";
import { Logo } from "@/components/shell";
import { Button } from "@/components/ui";
import { ApiError } from "@/lib/api";
import { login, safeNext, useMe } from "@/lib/auth";

const inputCls =
  "h-10 w-full rounded-lg border border-border bg-surface px-3 text-sm text-fg outline-none placeholder:text-subtle focus:border-accent focus:ring-2 focus:ring-accent/20";

export default function LoginPage() {
  const router = useRouter();
  const params = useSearchParams();
  const next = safeNext(params.get("next"));
  const { data: me, mutate } = useMe();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  // Already signed in, or no accounts exist yet: nothing to do here.
  useEffect(() => {
    if (me && (me.user || !me.auth_required)) router.replace(next);
  }, [me, next, router]);

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await login(email, password);
      await mutate();
      router.replace(next);
    } catch (err) {
      const status = err instanceof ApiError ? err.status : 0;
      setError(
        status === 401 ? "Wrong email or password."
          : status === 429 ? "Too many failed attempts. Wait a few minutes and try again."
          : status === 0 ? "Can't reach the Burnrate API. Is it running?"
          : "Something went wrong. Try again.",
      );
      setBusy(false);
    }
  }

  return (
    <div className="grid min-h-dvh place-items-center bg-bg px-4 py-10">
      <div className="w-full max-w-sm">
        <div className="mb-8 flex justify-center">
          <Logo />
        </div>
        <div className="rounded-2xl border border-border bg-surface p-6 shadow-sm sm:p-8">
          <h1 className="text-lg font-semibold tracking-tight text-fg">Sign in</h1>
          <p className="mt-1 text-sm text-muted">See where your team&apos;s AI spend goes.</p>
          <form onSubmit={onSubmit} className="mt-6 space-y-4" noValidate>
            <label className="flex flex-col gap-1.5">
              <span className="text-xs font-medium text-fg-2">Email</span>
              <input
                className={inputCls}
                type="email"
                autoComplete="username"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                placeholder="you@company.com"
                required
                autoFocus
              />
            </label>
            <label className="flex flex-col gap-1.5">
              <span className="text-xs font-medium text-fg-2">Password</span>
              <input
                className={inputCls}
                type="password"
                autoComplete="current-password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                required
              />
            </label>
            {error && (
              <p role="alert" className="rounded-lg bg-bad-soft px-3 py-2 text-sm text-bad">
                {error}
              </p>
            )}
            <Button type="submit" variant="primary" className="h-10 w-full" disabled={busy || !email || !password}>
              <LogIn className="size-4" aria-hidden />
              {busy ? "Signing in…" : "Sign in"}
            </Button>
          </form>
        </div>
        <p className="mt-6 text-center text-xs text-subtle">
          No account? Ask your Burnrate admin to run <code className="whitespace-nowrap font-mono">burnrate create-user</code>.
        </p>
      </div>
    </div>
  );
}
