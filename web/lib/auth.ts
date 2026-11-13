import { request, useApi } from "./api";

export type Role = "superadmin" | "admin" | "member" | "viewer";

/** Both run Settings; only a super admin can add or change admins and super admins. */
export const isAdminRole = (role: Role) => role === "superadmin" || role === "admin";

export interface User {
  id: number;
  email: string;
  name: string | null;
  role: Role;
  /** null = every team */
  teams: string[] | null;
}

export interface Me {
  /** False only while no user account exists (open local demo). */
  auth_required: boolean;
  user: User | null;
}

export function useMe() {
  return useApi<Me>("/api/auth/me", { shouldRetryOnError: false });
}

export function login(email: string, password: string) {
  return request<{ user: User }>("/api/auth/login", { method: "POST", body: JSON.stringify({ email, password }) });
}

export function logout() {
  return request<void>("/api/auth/logout", { method: "POST" });
}

/** Only same-site paths, so `?next=` can't send someone to another website after signing in. */
export function safeNext(next: string | null): string {
  return next && next.startsWith("/") && !next.startsWith("//") && !next.startsWith("/login") ? next : "/";
}

export interface Permissions {
  ready: boolean;
  role: Role;
  /** Teams this person is limited to; null = all. */
  teams: string[] | null;
  isAdmin: boolean;
  isSuperAdmin: boolean;
  canEdit: boolean;
  scoped: boolean;
}

/** What the current visitor may do. With no accounts set up (open local demo) that's everything. */
export function usePermissions(): Permissions {
  const { data } = useMe();
  const user = data?.user;
  const role: Role = user ? user.role : "superadmin";
  const teams = user ? user.teams : null;
  return { ready: Boolean(data), role, teams, isAdmin: isAdminRole(role), isSuperAdmin: role === "superadmin", canEdit: role !== "viewer", scoped: teams !== null };
}

export function acceptInvite(token: string, password: string) {
  return request<{ user: User }>("/api/auth/invite", { method: "POST", body: JSON.stringify({ token, password }) });
}

export const ROLE_LABEL: Record<Role, string> = { superadmin: "Super admin", admin: "Admin", member: "Member", viewer: "Viewer" };
export const ROLE_HELP: Record<Role, string> = {
  superadmin: "Everything, including adding and changing admins",
  admin: "Users, API keys and settings, but not other admins",
  member: "Sees spend and manages budgets",
  viewer: "Can look, can't change anything",
};
