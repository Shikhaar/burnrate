"use client";

import clsx from "clsx";
import { BellRing, Check, ChevronRight, Copy, ExternalLink, KeyRound, Link2, Plus, Send, Ticket, Trash2, Unplug, UserPlus, Users, X } from "lucide-react";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { useState, type FormEvent, type ReactNode } from "react";
import { Badge, Button, Card, CardBody, CardHeader, DataState, EmptyState, PageHeader, Segmented, TableSkeleton } from "@/components/ui";
import {
  ApiError,
  connectJira,
  createJiraTestTicket,
  createKey,
  deleteKey,
  disconnectJira,
  disconnectWebhook,
  inviteUser,
  newInviteLink,
  revokeKey,
  saveWebhook,
  sendTestAlert,
  updateUser,
  useAlertSettings,
  useJiraSettings,
  useKeys,
  useMeta,
  useUsers,
  type AdminUser,
  type IngestKey,
  type InviteLink,
} from "@/lib/api";
import { ROLE_HELP, ROLE_LABEL, useMe, usePermissions, type Role } from "@/lib/auth";
import { timestamp } from "@/lib/format";

const TABS = [
  { value: "alerts", label: "Alerts" },
  { value: "users", label: "Users" },
  { value: "keys", label: "API keys" },
] as const;
type Tab = (typeof TABS)[number]["value"];

const inputCls =
  "h-9 w-full rounded-lg border border-border bg-surface px-3 text-sm text-fg outline-none placeholder:text-subtle focus:border-accent focus:ring-2 focus:ring-accent/20";

const errorText = (e: unknown, fallback: string) => (e instanceof ApiError ? e.message.replace(/^\d+\s*/, "") : fallback);
const when = (ts: string | null, never = "Never") => (ts ? timestamp(ts) : never);

export default function SettingsPage() {
  const perms = usePermissions();
  const params = useSearchParams();
  const router = useRouter();
  const pathname = usePathname();
  const tab = (TABS.find((t) => t.value === params.get("tab"))?.value ?? "alerts") as Tab;

  if (perms.ready && !perms.isAdmin) {
    return (
      <>
        <PageHeader title="Settings" />
        <Card>
          <EmptyState title="Admins only" description="Ask a Burnrate admin to change alerts, users or API keys." />
        </Card>
      </>
    );
  }

  return (
    <>
      <PageHeader
        title="Settings"
        description="Alerts, who can sign in, and the keys your apps use to send usage."
        actions={
          <Segmented
            ariaLabel="Settings section"
            value={tab}
            onChange={(v) => router.replace(`${pathname}?tab=${v}`)}
            options={TABS.map((t) => ({ value: t.value, label: t.label }))}
          />
        }
      />
      {tab === "alerts" ? <AlertsTab /> : tab === "users" ? <UsersTab /> : <KeysTab />}
    </>
  );
}

/* ---------------- Alerts (Slack, Jira) ---------------- */

function AlertsTab() {
  return (
    <div className="space-y-4">
      <SlackSection />
      <JiraSection />
    </div>
  );
}

function SlackSection() {
  const q = useAlertSettings();
  const [url, setUrl] = useState("");
  const [busy, setBusy] = useState<null | "save" | "test" | "remove">(null);
  const [msg, setMsg] = useState<{ tone: "ok" | "bad"; text: string } | null>(null);

  const run = async (kind: "save" | "test" | "remove", fn: () => Promise<unknown>, ok: string) => {
    setBusy(kind);
    setMsg(null);
    try {
      await fn();
      await q.mutate();
      setMsg({ tone: "ok", text: ok });
      if (kind === "save") setUrl("");
    } catch (e) {
      setMsg({ tone: "bad", text: errorText(e, "Something went wrong.") });
    } finally {
      setBusy(null);
    }
  };

  const onSave = (e: FormEvent) => {
    e.preventDefault();
    void run("save", () => saveWebhook(url), "Connected. A test message was posted to the channel.");
  };

  return (
    <div className="grid grid-cols-1 gap-4 xl:grid-cols-[minmax(0,3fr)_minmax(0,2fr)]">
      <Card>
        <CardHeader
          title="Slack alerts"
          description="Burnrate posts here when a budget is at risk or exceeded, or spend suddenly jumps."
        />
        <CardBody>
          <DataState query={q} loading={<TableSkeleton rows={2} cols={2} />}>
            {(s) => (
              <div className="space-y-5">
                <div className="flex flex-wrap items-center gap-3 rounded-lg border border-border bg-surface-2/40 p-3">
                  <BellRing className={clsx("size-5", s.connected ? "text-ok" : "text-subtle")} aria-hidden />
                  <div className="min-w-0 flex-1">
                    <p className="text-sm font-medium text-fg">
                      {s.connected ? (s.is_slack ? "Connected to Slack" : "Connected to a webhook") : "Not connected"}
                    </p>
                    <p className="truncate font-mono text-xs text-muted">
                      {s.connected
                        ? `${s.webhook_hint}${s.source === "environment" ? " · set on the server (BURNRATE_ALERT_WEBHOOK_URL)" : ""}`
                        : "Alerts are recorded on the Budgets page but not sent anywhere."}
                    </p>
                  </div>
                  {s.connected ? (
                    <div className="flex gap-2">
                      <Button size="sm" disabled={busy !== null} onClick={() => void run("test", sendTestAlert, "Test message sent. Check the channel.")}>
                        <Send className="size-3.5" aria-hidden /> {busy === "test" ? "Sending…" : "Send test"}
                      </Button>
                      {s.source === "dashboard" ? (
                        <Button size="sm" variant="danger" disabled={busy !== null} onClick={() => void run("remove", disconnectWebhook, "Disconnected.")}>
                          <Unplug className="size-3.5" aria-hidden /> Disconnect
                        </Button>
                      ) : null}
                    </div>
                  ) : null}
                </div>

                <form onSubmit={onSave} className="space-y-2">
                  <label className="flex flex-col gap-1.5">
                    <span className="text-xs font-medium text-fg-2">{s.connected ? "Switch to a different webhook" : "Slack webhook link"}</span>
                    <div className="flex flex-col gap-2 sm:flex-row">
                      <input
                        className={clsx(inputCls, "font-mono")}
                        value={url}
                        onChange={(e) => setUrl(e.target.value)}
                        placeholder="https://hooks.slack.com/services/…"
                        autoComplete="off"
                        spellCheck={false}
                      />
                      <Button type="submit" variant="primary" className="h-9 shrink-0" disabled={!url.trim() || busy !== null}>
                        {busy === "save" ? "Checking…" : s.connected ? "Switch" : "Connect"}
                      </Button>
                    </div>
                  </label>
                  <p className="text-xs text-muted">
                    Burnrate posts a test message first and only saves the link if it arrives. The full link is never shown again.
                  </p>
                </form>

                {msg ? (
                  <p role="status" className={clsx("rounded-lg px-3 py-2 text-sm", msg.tone === "ok" ? "bg-ok-soft text-ok" : "bg-bad-soft text-bad")}>
                    {msg.text}
                  </p>
                ) : null}

                <dl className="grid grid-cols-1 gap-3 border-t border-border pt-4 text-sm sm:grid-cols-2">
                  <Stat label="Checks for new alerts">
                    {s.timer.enabled ? `Every ${s.timer.interval_minutes} minutes` : "Off (run `burnrate alerts` from cron)"}
                  </Stat>
                  <Stat label="Last check">{when(s.timer.last_run_at, "Not yet")}</Stat>
                  <Stat label="Last alert delivered">{when(s.last_delivered_at)}</Stat>
                  <Stat label="Waiting to be sent">{s.undelivered}</Stat>
                </dl>
                {s.timer.last_run_error ? <Problem title="The last check failed">{s.timer.last_run_error}</Problem> : null}
                {s.last_failure ? (
                  <Problem title={`Couldn't deliver “${s.last_failure.title}”`}>{s.last_failure.error}</Problem>
                ) : null}
              </div>
            )}
          </DataState>
        </CardBody>
      </Card>

      <Card>
        <CardHeader title="Get a Slack webhook link" description="About two minutes, done once." />
        <CardBody>
          <ol className="list-decimal space-y-2 pl-5 text-sm text-fg-2">
            <li>
              Open <span className="font-mono text-xs">api.slack.com/apps</span> and choose <b>Create New App → From scratch</b>. Name it
              “Burnrate” and pick your workspace.
            </li>
            <li>
              Open <b>Incoming Webhooks</b>, switch it on, then <b>Add New Webhook to Workspace</b>.
            </li>
            <li>Pick the channel for alerts, for example #ai-spend.</li>
            <li>
              Copy the link Slack shows. Paste it into the <b>Slack webhook link</b> box in the Slack alerts section and click <b>Connect</b>.
              Treat the link like a password: anyone with it can post to that channel.
            </li>
          </ol>
          <p className="mt-3 text-xs text-muted">Teams, Discord and other tools that accept Slack-style webhooks work too.</p>
        </CardBody>
      </Card>
    </div>
  );
}

function JiraSection() {
  const q = useJiraSettings();
  const s = q.data;
  const [editing, setEditing] = useState(false);
  const [form, setForm] = useState({ site: "", email: "", api_token: "", project_key: "", issue_type: "Task", min_severity: "high" as "high" | "medium" });
  const [busy, setBusy] = useState<null | "save" | "test" | "remove">(null);
  const [msg, setMsg] = useState<{ tone: "ok" | "bad"; text: ReactNode } | null>(null);
  const set = (k: keyof typeof form) => (e: { target: { value: string } }) => setForm((f) => ({ ...f, [k]: e.target.value }));

  const startEdit = () => {
    if (s?.connected) {
      setForm({ site: s.site ?? "", email: s.email ?? "", api_token: "", project_key: s.project_key ?? "", issue_type: s.issue_type ?? "Task", min_severity: s.min_severity });
    }
    setMsg(null);
    setEditing(true);
  };

  const save = async (e: FormEvent) => {
    e.preventDefault();
    setBusy("save");
    setMsg(null);
    try {
      const r = await connectJira({ ...form, api_token: form.api_token || null });
      await q.mutate();
      setEditing(false);
      setForm((f) => ({ ...f, api_token: "" }));
      setMsg({ tone: "ok", text: `Connected to ${r.verified.project_name ?? r.project_key} as ${r.verified.account ?? r.email}. Nothing was created.` });
    } catch (err) {
      setMsg({ tone: "bad", text: errorText(err, "Couldn't connect to Jira.") });
    } finally {
      setBusy(null);
    }
  };

  const testTicket = async () => {
    setBusy("test");
    setMsg(null);
    try {
      const r = await createJiraTestTicket();
      setMsg({ tone: "ok", text: <>Created <a className="underline" href={r.url} target="_blank" rel="noreferrer">{r.key}</a>. You can delete it in Jira.</> });
    } catch (err) {
      setMsg({ tone: "bad", text: errorText(err, "Couldn't create a ticket.") });
    } finally {
      setBusy(null);
    }
  };

  const remove = async () => {
    setBusy("remove");
    try {
      await disconnectJira();
      await q.mutate();
      setMsg({ tone: "ok", text: "Disconnected. No more tickets will be created." });
    } catch (err) {
      setMsg({ tone: "bad", text: errorText(err, "Couldn't disconnect.") });
    } finally {
      setBusy(null);
    }
  };

  const showForm = editing || (s && !s.connected);
  return (
    <div className="grid grid-cols-1 gap-4 xl:grid-cols-[minmax(0,3fr)_minmax(0,2fr)]">
      <Card>
        <CardHeader title="Jira tickets" description="Burnrate files a ticket for serious alerts, one per problem, so someone owns the fix." />
        <CardBody>
          <DataState query={q} loading={<TableSkeleton rows={2} cols={2} />}>
            {(s) => (
              <div className="space-y-5">
                <div className="flex flex-wrap items-center gap-3 rounded-lg border border-border bg-surface-2/40 p-3">
                  <Ticket className={clsx("size-5", s.connected ? "text-ok" : "text-subtle")} aria-hidden />
                  <div className="min-w-0 flex-1">
                    <p className="text-sm font-medium text-fg">{s.connected ? `Connected to project ${s.project_key}` : "Not connected"}</p>
                    <p className="truncate text-xs text-muted">
                      {s.connected
                        ? `${s.site?.replace("https://", "")} · as ${s.email} · token ${s.token_hint} · ${s.issue_type} tickets`
                        : "Alerts won't create Jira tickets."}
                    </p>
                  </div>
                  {s.connected && !editing ? (
                    <div className="flex flex-wrap gap-2">
                      <Button size="sm" disabled={busy !== null} onClick={() => void testTicket()}>
                        <Send className="size-3.5" aria-hidden /> {busy === "test" ? "Creating…" : "Create test ticket"}
                      </Button>
                      <Button size="sm" variant="ghost" disabled={busy !== null} onClick={startEdit}>
                        Edit
                      </Button>
                      <Button size="sm" variant="danger" disabled={busy !== null} onClick={() => void remove()}>
                        <Unplug className="size-3.5" aria-hidden /> Disconnect
                      </Button>
                    </div>
                  ) : null}
                </div>

                {showForm ? (
                  <form onSubmit={save} className="space-y-4">
                    <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
                      <Labeled label="Jira address">
                        <input className={inputCls} value={form.site} onChange={set("site")} placeholder="acme.atlassian.net" autoComplete="off" spellCheck={false} />
                      </Labeled>
                      <Labeled label="Jira account email">
                        <input className={inputCls} type="email" value={form.email} onChange={set("email")} placeholder="burnrate-bot@acme.com" autoComplete="off" />
                      </Labeled>
                      <Labeled label={s.connected ? "API token (leave empty to keep the current one)" : "API token"}>
                        <input className={clsx(inputCls, "font-mono")} type="password" value={form.api_token} onChange={set("api_token")} autoComplete="new-password" />
                      </Labeled>
                      <Labeled label="Project key">
                        <input className={clsx(inputCls, "uppercase")} value={form.project_key} onChange={set("project_key")} placeholder="OPS" maxLength={20} />
                      </Labeled>
                      <Labeled label="Ticket type">
                        <input className={inputCls} value={form.issue_type} onChange={set("issue_type")} placeholder="Task" maxLength={60} />
                      </Labeled>
                      <Labeled label="Which alerts become tickets">
                        <select className={inputCls} value={form.min_severity} onChange={set("min_severity")}>
                          <option value="high">Only serious ones (budget exceeded, big spikes)</option>
                          <option value="medium">Serious and warnings (also budgets at risk)</option>
                        </select>
                      </Labeled>
                    </div>
                    <div className="flex flex-wrap items-center gap-2">
                      <Button type="submit" variant="primary" disabled={busy !== null || !form.site || !form.email || !form.project_key || (!s.connected && !form.api_token)}>
                        {busy === "save" ? "Checking…" : s.connected ? "Save" : "Connect"}
                      </Button>
                      {editing ? (
                        <Button variant="ghost" onClick={() => setEditing(false)}>
                          Cancel
                        </Button>
                      ) : null}
                      <p className="text-xs text-muted">Burnrate checks the login, project and ticket type before saving. It doesn&apos;t create anything.</p>
                    </div>
                  </form>
                ) : null}

                {msg ? (
                  <p role="status" className={clsx("rounded-lg px-3 py-2 text-sm", msg.tone === "ok" ? "bg-ok-soft text-ok" : "bg-bad-soft text-bad")}>
                    {msg.text}
                  </p>
                ) : null}

                {s.connected ? (
                  <dl className="grid grid-cols-1 gap-3 border-t border-border pt-4 text-sm sm:grid-cols-2">
                    <Stat label="Creates tickets for">{s.min_severity === "high" ? "Serious alerts only" : "Serious alerts and warnings"}</Stat>
                    <Stat label="Last ticket">
                      {s.last_ticket ? (
                        <a className="inline-flex items-center gap-1 text-accent hover:underline" href={s.last_ticket.url ?? "#"} target="_blank" rel="noreferrer">
                          {s.last_ticket.key} <ExternalLink className="size-3" aria-hidden />
                        </a>
                      ) : (
                        "None yet"
                      )}
                    </Stat>
                  </dl>
                ) : null}
                {s.last_failure ? <Problem title={`Couldn't file “${s.last_failure.title}”`}>{s.last_failure.error}</Problem> : null}
              </div>
            )}
          </DataState>
        </CardBody>
      </Card>

      <Card>
        <CardHeader title="Get a Jira API token" description="About five minutes, done once." />
        <CardBody>
          <ol className="list-decimal space-y-2 pl-5 text-sm text-fg-2">
            <li>
              Best: create a Jira user just for Burnrate (e.g. burnrate-bot@yourcompany.com) that can only <b>create issues</b> in the project
              you&apos;ll use. The token can do whatever that user can.
            </li>
            <li>
              Signed in as that user, open <span className="font-mono text-xs">id.atlassian.com/manage-profile/security/api-tokens</span> and
              choose <b>Create API token</b>.
            </li>
            <li>
              Copy the token and paste it into the <b>API token</b> box in the Jira tickets section, with that user&apos;s email, your Jira address
              and the project key (the letters before ticket numbers, like <b>OPS</b> in OPS-12).
            </li>
            <li>
              Click <b>Connect</b>, then <b>Create test ticket</b> to see where alerts will land.
            </li>
          </ol>
          <p className="mt-3 text-xs text-muted">The token is stored on the Burnrate server and never shown again; only its last four characters are.</p>
        </CardBody>
      </Card>
    </div>
  );
}

function Labeled({ label, children }: { label: string; children: ReactNode }) {
  return (
    <label className="flex flex-col gap-1.5">
      <span className="text-xs font-medium text-fg-2">{label}</span>
      {children}
    </label>
  );
}

function Stat({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div>
      <dt className="text-xs text-subtle">{label}</dt>
      <dd className="mt-0.5 font-medium text-fg">{children}</dd>
    </div>
  );
}

function Problem({ title, children }: { title: string; children: ReactNode }) {
  return (
    <div role="alert" className="rounded-lg bg-bad-soft px-3 py-2 text-sm text-bad">
      <p className="font-medium">{title}</p>
      <p className="mt-0.5 break-words font-mono text-xs">{children}</p>
    </div>
  );
}

/* ---------------- Users ---------------- */

function UsersTab() {
  const q = useUsers();
  const me = useMe().data?.user;
  const [showForm, setShowForm] = useState(false);
  const [link, setLink] = useState<{ email: string; url: string; expires: string; reset: boolean } | null>(null);

  const showLink = (l: InviteLink, reset: boolean) =>
    setLink({ email: l.user.email, url: window.location.origin + l.invite_path, expires: l.invite_expires_at, reset });

  return (
    <div className="space-y-4">
      {link ? (
        <OneTimeSecret
          title={link.reset ? `Password reset link for ${link.email}` : `Invite link for ${link.email}`}
          description={`Send this link to them yourself (Burnrate doesn't send email). It works once and expires ${timestamp(link.expires)}.`}
          secret={link.url}
          onClose={() => setLink(null)}
        />
      ) : null}

      <Card>
        <CardHeader
          title="People"
          description="Everyone who can sign in. New people get a link to set their own password."
          actions={
            <Button size="sm" variant={showForm ? "secondary" : "primary"} onClick={() => setShowForm((v) => !v)}>
              {showForm ? <X className="size-3.5" aria-hidden /> : <UserPlus className="size-3.5" aria-hidden />}
              {showForm ? "Cancel" : "Add person"}
            </Button>
          }
        />
        <CardBody>
          {showForm ? (
            <InviteForm
              onInvited={async (l) => {
                setShowForm(false);
                showLink(l, false);
                await q.mutate();
              }}
            />
          ) : null}
          <DataState query={q} loading={<TableSkeleton rows={3} cols={4} />} isEmpty={(d) => d.items.length === 0}
            empty={<EmptyState icon={<Users className="size-5" aria-hidden />} title="No users yet" description="Add the first person to require signing in." />}>
            {(d) => (
              <ul className="divide-y divide-border">
                {d.items.map((u) => (
                  <UserRow key={u.id} user={u} isMe={me?.id === u.id} onChanged={() => void q.mutate()} onLink={(l) => showLink(l, !u.invite_pending)} />
                ))}
              </ul>
            )}
          </DataState>
        </CardBody>
      </Card>

      <Card>
        <CardBody className="pt-4">
          <dl className="grid grid-cols-1 gap-3 text-sm sm:grid-cols-3">
            {(Object.keys(ROLE_LABEL) as Role[]).map((r) => (
              <div key={r}>
                <dt className="font-medium text-fg">{ROLE_LABEL[r]}</dt>
                <dd className="text-xs text-muted">{ROLE_HELP[r]}</dd>
              </div>
            ))}
          </dl>
          <p className="mt-3 text-xs text-muted">
            Limit a member or viewer to some teams and every page shows only those teams&apos; spend. They won&apos;t see provider bills or
            company-wide alerts.
          </p>
        </CardBody>
      </Card>
    </div>
  );
}

function TeamPicker({ value, onChange }: { value: string[] | null; onChange: (v: string[] | null) => void }) {
  const meta = useMeta();
  const known = meta.data?.teams ?? [];
  const options = Array.from(new Set([...known, ...(value ?? [])])).sort();
  const toggle = (t: string) => {
    const cur = new Set(value ?? []);
    if (cur.has(t)) cur.delete(t);
    else cur.add(t);
    onChange(Array.from(cur).sort());
  };
  return (
    <div className="space-y-2">
      <label className="flex items-center gap-2 text-sm text-fg-2">
        <input type="checkbox" checked={value === null} onChange={(e) => onChange(e.target.checked ? null : [])} />
        All teams
      </label>
      {value !== null ? (
        options.length ? (
          <div className="flex flex-wrap gap-2">
            {options.map((t) => (
              <button
                key={t}
                type="button"
                onClick={() => toggle(t)}
                aria-pressed={value.includes(t)}
                className={clsx(
                  "rounded-full border px-2.5 py-1 text-xs font-medium",
                  value.includes(t) ? "border-accent bg-accent-soft text-accent" : "border-border text-muted hover:text-fg",
                )}
              >
                {value.includes(t) ? <Check className="mr-1 inline size-3" aria-hidden /> : null}
                {t}
              </button>
            ))}
          </div>
        ) : (
          <p className="text-xs text-muted">No teams have sent usage yet. Teams come from the `team` field apps send.</p>
        )
      ) : null}
    </div>
  );
}

function InviteForm({ onInvited }: { onInvited: (l: InviteLink) => Promise<void> }) {
  const [email, setEmail] = useState("");
  const [name, setName] = useState("");
  const [role, setRole] = useState<Role>("member");
  const [teams, setTeams] = useState<string[] | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    if (teams && teams.length === 0) return setError("Pick at least one team, or tick All teams.");
    setBusy(true);
    setError(null);
    try {
      await onInvited(await inviteUser({ email, name: name || undefined, role, teams: role === "admin" ? null : teams }));
    } catch (err) {
      setError(errorText(err, "Couldn't add this person."));
    } finally {
      setBusy(false);
    }
  };

  return (
    <form onSubmit={submit} className="mb-5 space-y-4 rounded-lg border border-border bg-surface-2/40 p-4">
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
        <label className="flex flex-col gap-1.5">
          <span className="text-xs font-medium text-fg-2">Email</span>
          <input className={inputCls} type="email" value={email} onChange={(e) => setEmail(e.target.value)} placeholder="ana@company.com" autoFocus required />
        </label>
        <label className="flex flex-col gap-1.5">
          <span className="text-xs font-medium text-fg-2">Name (optional)</span>
          <input className={inputCls} value={name} onChange={(e) => setName(e.target.value)} maxLength={100} />
        </label>
        <label className="flex flex-col gap-1.5">
          <span className="text-xs font-medium text-fg-2">Role</span>
          <select className={inputCls} value={role} onChange={(e) => setRole(e.target.value as Role)}>
            {(Object.keys(ROLE_LABEL) as Role[]).map((r) => (
              <option key={r} value={r}>
                {ROLE_LABEL[r]} — {ROLE_HELP[r]}
              </option>
            ))}
          </select>
        </label>
      </div>
      {role !== "admin" ? (
        <div>
          <p className="mb-1.5 text-xs font-medium text-fg-2">Can see</p>
          <TeamPicker value={teams} onChange={setTeams} />
        </div>
      ) : (
        <p className="text-xs text-muted">Admins always see every team.</p>
      )}
      {error ? <p role="alert" className="rounded-lg bg-bad-soft px-3 py-2 text-sm text-bad">{error}</p> : null}
      <Button type="submit" variant="primary" disabled={busy || !email}>
        <Plus className="size-4" aria-hidden /> {busy ? "Adding…" : "Add and get invite link"}
      </Button>
    </form>
  );
}

function UserRow({ user: u, isMe, onChanged, onLink }: { user: AdminUser; isMe: boolean; onChanged: () => void; onLink: (l: InviteLink) => void }) {
  const [editing, setEditing] = useState(false);
  const [role, setRole] = useState<Role>(u.role);
  const [teams, setTeams] = useState<string[] | null>(u.teams);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const act = async (fn: () => Promise<unknown>) => {
    setBusy(true);
    setError(null);
    try {
      await fn();
      onChanged();
      return true;
    } catch (e) {
      setError(errorText(e, "That didn't work."));
      return false;
    } finally {
      setBusy(false);
    }
  };

  const save = async () => {
    if (role !== "admin" && teams && teams.length === 0) return setError("Pick at least one team, or tick All teams.");
    if (await act(() => updateUser(u.id, { role, teams: role === "admin" ? null : teams }))) setEditing(false);
  };

  return (
    <li className="py-3">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
        <div className="min-w-0 flex-1 basis-full sm:basis-0">
          <p className="truncate text-sm font-medium text-fg">
            {u.name || u.email}
            {isMe ? <span className="ml-1.5 text-xs font-normal text-subtle">(you)</span> : null}
          </p>
          <p className="truncate text-xs text-muted">
            {u.name ? `${u.email} · ` : ""}
            {u.invite_pending ? "Hasn't set a password yet" : `Last signed in ${when(u.last_login_at)}`}
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-1.5">
          <Badge tone={u.role === "admin" ? "accent" : "neutral"}>{ROLE_LABEL[u.role]}</Badge>
          <Badge>{u.teams ? u.teams.join(", ") : "All teams"}</Badge>
          {u.disabled ? <Badge tone="bad">Disabled</Badge> : u.invite_pending ? <Badge tone="warn">Invited</Badge> : null}
        </div>
        <div className="flex flex-wrap gap-1.5">
          {!u.disabled ? (
            <Button size="sm" variant="ghost" disabled={busy} onClick={() => void act(async () => onLink(await newInviteLink(u.id)))}>
              <Link2 className="size-3.5" aria-hidden /> {u.invite_pending ? "New invite link" : "Reset password"}
            </Button>
          ) : null}
          {!isMe ? (
            <>
              <Button size="sm" variant="ghost" disabled={busy} onClick={() => setEditing((v) => !v)}>
                {editing ? "Cancel" : "Edit"}
              </Button>
              <Button size="sm" variant={u.disabled ? "secondary" : "danger"} disabled={busy} onClick={() => void act(() => updateUser(u.id, { disabled: !u.disabled }))}>
                {u.disabled ? "Enable" : "Disable"}
              </Button>
            </>
          ) : null}
        </div>
      </div>
      {editing ? (
        <div className="mt-3 space-y-3 rounded-lg border border-border bg-surface-2/40 p-3">
          <label className="flex max-w-xs flex-col gap-1.5">
            <span className="text-xs font-medium text-fg-2">Role</span>
            <select className={inputCls} value={role} onChange={(e) => setRole(e.target.value as Role)}>
              {(Object.keys(ROLE_LABEL) as Role[]).map((r) => (
                <option key={r} value={r}>
                  {ROLE_LABEL[r]}
                </option>
              ))}
            </select>
          </label>
          {role !== "admin" ? <TeamPicker value={teams} onChange={setTeams} /> : <p className="text-xs text-muted">Admins always see every team.</p>}
          <Button size="sm" variant="primary" disabled={busy} onClick={() => void save()}>
            {busy ? "Saving…" : "Save"}
          </Button>
        </div>
      ) : null}
      {error ? <p role="alert" className="mt-2 rounded-lg bg-bad-soft px-3 py-2 text-sm text-bad">{error}</p> : null}
    </li>
  );
}

/* ---------------- API keys ---------------- */

function KeysTab() {
  const q = useKeys();
  const [name, setName] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [fresh, setFresh] = useState<{ name: string; secret: string } | null>(null);
  const [showRevoked, setShowRevoked] = useState(false);

  const create = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const r = await createKey(name.trim());
      setFresh({ name: r.key.name, secret: r.secret });
      setName("");
      await q.mutate();
    } catch (err) {
      setError(errorText(err, "Couldn't create the key."));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="space-y-4">
      {fresh ? (
        <OneTimeSecret
          title={`New key for ${fresh.name}`}
          description="Copy it now and give it to the team that runs this app. It won't be shown again. It can only send usage: it can't read spend or reach your AI providers."
          secret={fresh.secret}
          onClose={() => setFresh(null)}
        />
      ) : null}
      <Card>
        <CardHeader title="API keys" description="One key per app or service. Apps use it to send usage to Burnrate." />
        <CardBody>
          <form onSubmit={create} className="mb-4 flex flex-col gap-2 sm:flex-row">
            <input className={inputCls} value={name} onChange={(e) => setName(e.target.value)} placeholder="Which app is this for? e.g. support chatbot" maxLength={100} />
            <Button type="submit" variant="primary" className="h-9 shrink-0" disabled={busy || !name.trim()}>
              <KeyRound className="size-4" aria-hidden /> {busy ? "Creating…" : "Create key"}
            </Button>
          </form>
          {error ? <p role="alert" className="mb-3 rounded-lg bg-bad-soft px-3 py-2 text-sm text-bad">{error}</p> : null}
          <DataState query={q} loading={<TableSkeleton rows={3} cols={4} />} isEmpty={(d) => d.items.length === 0}
            empty={<EmptyState icon={<KeyRound className="size-5" aria-hidden />} title="No keys yet" description="Create one for each app that calls an AI model." />}>
            {(d) => {
              const newestFirst = [...d.items].reverse();
              const active = newestFirst.filter((k) => !k.revoked_at);
              const revoked = newestFirst.filter((k) => k.revoked_at);
              return (
                <>
                  {active.length ? (
                    <ul className="divide-y divide-border">
                      {active.map((k) => (
                        <KeyRow key={k.id} k={k} onChanged={() => void q.mutate()} onError={setError} />
                      ))}
                    </ul>
                  ) : (
                    <p className="py-3 text-sm text-muted">No active keys. Create one above for each app that sends usage.</p>
                  )}
                  {revoked.length ? (
                    <div className="mt-3 border-t border-border pt-3">
                      <button
                        type="button"
                        onClick={() => setShowRevoked((v) => !v)}
                        aria-expanded={showRevoked}
                        className="inline-flex items-center gap-1.5 text-xs font-medium text-muted hover:text-fg"
                      >
                        <ChevronRight className={clsx("size-3.5 transition-transform", showRevoked && "rotate-90")} aria-hidden />
                        Revoked keys ({revoked.length})
                      </button>
                      {showRevoked ? (
                        <ul className="mt-1 divide-y divide-border">
                          {revoked.map((k) => (
                            <KeyRow key={k.id} k={k} onChanged={() => void q.mutate()} onError={setError} />
                          ))}
                        </ul>
                      ) : null}
                    </div>
                  ) : null}
                </>
              );
            }}
          </DataState>
        </CardBody>
      </Card>
    </div>
  );
}

function KeyRow({ k, onChanged, onError }: { k: IngestKey; onChanged: () => void; onError: (msg: string | null) => void }) {
  const [confirm, setConfirm] = useState<null | "revoke" | "delete">(null);
  const [busy, setBusy] = useState(false);

  const run = async (fn: () => Promise<unknown>, fallback: string) => {
    setBusy(true);
    onError(null);
    try {
      await fn();
      setConfirm(null);
      onChanged();
    } catch (err) {
      onError(errorText(err, fallback));
    } finally {
      setBusy(false);
    }
  };

  return (
    <li className="flex flex-wrap items-center gap-x-3 gap-y-2 py-3">
      <div className="min-w-0 flex-1 basis-full sm:basis-0">
        <p className={clsx("truncate text-sm font-medium", k.revoked_at ? "text-muted" : "text-fg")}>{k.name}</p>
        <p className="truncate text-xs text-muted">
          <span className="font-mono">{k.prefix}…</span> · created {timestamp(k.created_at)} · last used {when(k.last_used_at)}
        </p>
      </div>
      {confirm ? (
        <div className="flex flex-wrap items-center gap-1.5">
          <span className="text-xs text-muted">
            {confirm === "revoke" ? "Apps using it will stop reporting." : "Removes it from this list for good."}
          </span>
          <Button size="sm" variant="ghost" disabled={busy} onClick={() => setConfirm(null)}>
            Cancel
          </Button>
          <Button
            size="sm"
            variant="destructive"
            disabled={busy}
            onClick={() =>
              void (confirm === "revoke"
                ? run(() => revokeKey(k.id), "Couldn't revoke the key.")
                : run(() => deleteKey(k.id), "Couldn't delete the key."))
            }
          >
            {confirm === "revoke" ? "Revoke" : "Delete"}
          </Button>
        </div>
      ) : k.revoked_at ? (
        <div className="flex items-center gap-1.5">
          <Badge tone="bad">Revoked {timestamp(k.revoked_at)}</Badge>
          <Button size="sm" variant="danger" onClick={() => setConfirm("delete")} aria-label={`Delete key ${k.name}`}>
            <Trash2 className="size-3.5" aria-hidden /> Delete
          </Button>
        </div>
      ) : (
        <div className="flex items-center gap-1.5">
          <Badge tone="ok">Active</Badge>
          <Button size="sm" variant="danger" onClick={() => setConfirm("revoke")} aria-label={`Revoke key ${k.name}`}>
            Revoke
          </Button>
        </div>
      )}
    </li>
  );
}

/* ---------------- shared ---------------- */

function OneTimeSecret({ title, description, secret, onClose }: { title: string; description: string; secret: string; onClose: () => void }) {
  const [copied, setCopied] = useState(false);
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(secret);
      setCopied(true);
    } catch {
      /* clipboard blocked: the text is selectable */
    }
  };
  return (
    <Card className="border-accent/40 bg-accent-soft/30">
      <CardHeader
        title={title}
        description={description}
        actions={
          <Button size="sm" variant="ghost" onClick={onClose} aria-label="Dismiss">
            <X className="size-3.5" aria-hidden />
          </Button>
        }
      />
      <CardBody>
        <div className="flex flex-col gap-2 sm:flex-row">
          <code className="min-w-0 flex-1 select-all break-all rounded-lg border border-border bg-surface px-3 py-2 font-mono text-xs text-fg">{secret}</code>
          <Button variant="primary" className="h-9 shrink-0" onClick={() => void copy()}>
            {copied ? <Check className="size-4" aria-hidden /> : <Copy className="size-4" aria-hidden />}
            {copied ? "Copied" : "Copy"}
          </Button>
        </div>
      </CardBody>
    </Card>
  );
}
