"use client";

import { KeyRound } from "lucide-react";
import { useRouter, useSearchParams } from "next/navigation";
import { useState, type FormEvent } from "react";
import { Logo } from "@/components/shell";
import { Button } from "@/components/ui";
import { ApiError, useApi } from "@/lib/api";
import { acceptInvite } from "@/lib/auth";

const MIN = 10;
const inputCls =
  "h-10 w-full rounded-lg border border-border bg-surface px-3 text-sm text-fg outline-none placeholder:text-subtle focus:border-accent focus:ring-2 focus:ring-accent/20";

export default function InvitePage() {
  const router = useRouter();
  const token = useSearchParams().get("token") ?? "";
  const invite = useApi<{ email: string; name: string | null }>(
    token ? `/api/auth/invite?token=${encodeURIComponent(token)}` : null,
    { shouldRetryOnError: false },
  );
  const [password, setPassword] = useState("");
  const [repeat, setRepeat] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    if (password.length < MIN) return setError(`Use at least ${MIN} characters.`);
    if (password !== repeat) return setError("The two passwords don't match.");
    setBusy(true);
    setError(null);
    try {
      await acceptInvite(token, password);
      // Full load so every page starts with the new session.
      // eslint-disable-next-line @next/next/no-location-assign-relative-destination
      window.location.assign("/");
    } catch (err) {
      setError(err instanceof ApiError && err.status === 404 ? "This link has expired or was already used. Ask your admin for a new one."
        : err instanceof ApiError ? err.message.replace(/^\d+\s*/, "") : "Something went wrong. Try again.");
      setBusy(false);
    }
  }

  const invalid = !token || invite.error;
  return (
    <div className="grid min-h-dvh place-items-center bg-bg px-4 py-10">
      <div className="w-full max-w-sm">
        <div className="mb-8 flex justify-center">
          <Logo />
        </div>
        <div className="rounded-2xl border border-border bg-surface p-6 shadow-sm sm:p-8">
          {invalid ? (
            <>
              <h1 className="text-lg font-semibold tracking-tight text-fg">This link doesn&apos;t work</h1>
              <p className="mt-2 text-sm text-muted">It may have expired or already been used. Ask your Burnrate admin for a new one.</p>
              <Button className="mt-6 h-10 w-full" onClick={() => router.replace("/login")}>
                Go to sign in
              </Button>
            </>
          ) : (
            <>
              <h1 className="text-lg font-semibold tracking-tight text-fg">Set your password</h1>
              <p className="mt-1 text-sm text-muted">
                {invite.data ? (
                  <>
                    Welcome{invite.data.name ? `, ${invite.data.name}` : ""}. You&apos;ll sign in as <b className="text-fg">{invite.data.email}</b>.
                  </>
                ) : (
                  "Checking your link…"
                )}
              </p>
              <form onSubmit={onSubmit} className="mt-6 space-y-4" noValidate>
                {/* Lets password managers save the right username. */}
                <input type="email" autoComplete="username" value={invite.data?.email ?? ""} readOnly hidden />
                <label className="flex flex-col gap-1.5">
                  <span className="text-xs font-medium text-fg-2">New password</span>
                  <input className={inputCls} type="password" autoComplete="new-password" value={password} onChange={(e) => setPassword(e.target.value)} autoFocus />
                  <span className="text-xs text-subtle">At least {MIN} characters.</span>
                </label>
                <label className="flex flex-col gap-1.5">
                  <span className="text-xs font-medium text-fg-2">Repeat password</span>
                  <input className={inputCls} type="password" autoComplete="new-password" value={repeat} onChange={(e) => setRepeat(e.target.value)} />
                </label>
                {error ? <p role="alert" className="rounded-lg bg-bad-soft px-3 py-2 text-sm text-bad">{error}</p> : null}
                <Button type="submit" variant="primary" className="h-10 w-full" disabled={busy || !invite.data || !password || !repeat}>
                  <KeyRound className="size-4" aria-hidden /> {busy ? "Saving…" : "Set password and sign in"}
                </Button>
              </form>
            </>
          )}
        </div>
      </div>
    </div>
  );
}
