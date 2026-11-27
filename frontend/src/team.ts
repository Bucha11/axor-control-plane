// Whether this workspace has a team: someone besides the person signed in.
// Pairing is watching a teammate's agent — alone, there is nobody to pair
// with (people from outside the team are the paid plan), so the screens do not
// offer it. An open plane, a guest, or one still loading: offered (examples).
import { useQuery } from "@tanstack/react-query";
import { api, type Capability } from "./api";
import { useApp } from "./store";

export function useHasTeam(): boolean {
  const email = useApp((s) => s.identityEmail);
  const me = useQuery({ queryKey: ["identity-me"], queryFn: api.identityMe, enabled: !!email, retry: false });
  const org = me.data?.active_org ?? "";
  const members = useQuery({ queryKey: ["org-members", org], queryFn: () => api.orgMembers(org),
                             enabled: !!org, retry: false, staleTime: 60_000 });
  if (!email || !members.data) return true;
  return members.data.members.length > 1;
}

/** Whether the person here may do something to the team's agents beyond
 *  their own (the workspace's permissions). A plane without them answers as
 *  before: an owner or admin for what was theirs, anyone for the rest. */
export function useMay(): (cap: Capability) => boolean {
  const admin = useCanAdmin();
  const q = useQuery({ queryKey: ["permissions"], queryFn: api.permissions, retry: false,
                       staleTime: 30_000 });
  return (cap) => q.data?.you[cap]
    ?? (cap === "stop_others" || cap === "brake" || cap === "exceptions" ? admin : true);
}

/** Whether the person here decides the team's policy: an owner or admin — or
 *  anyone on an open plane, which has no roles (whoever runs it decides).
 *  Nobody signed in on a plane that needs a sign-in is not an admin: a guest
 *  was offered the audit tab and got a 403 (QA-238). A token that carries the
 *  admin scope (the deployment's own) still is. */
export function useCanAdmin(): boolean {
  const email = useApp((s) => s.identityEmail);
  const apiToken = useApp((s) => s.apiToken);
  const me = useQuery({ queryKey: ["identity-me"], queryFn: api.identityMe, enabled: !!email, retry: false });
  const st = useQuery({ queryKey: ["auth-status", apiToken], queryFn: api.authStatus, enabled: !email });
  const role = me.data?.memberships.find((m) => m.org_id === me.data?.active_org)?.role ?? me.data?.role;
  if (email) return role === "owner" || role === "admin";
  return !!st.data && (!st.data.auth_enabled || st.data.scopes.includes("admin"));
}

/** Whether a coding session is the signed-in person's own: its developer is
 *  them. An open plane (nobody signed in) has one operator, whose all are. */
export function useMine(): (developer?: string | null) => boolean {
  const email = (useApp((s) => s.identityEmail) || "").toLowerCase();
  // a developer that is an opaque id (a machine key) names nobody: the team's
  return (developer) => !email || !developer || !developer.includes("@") || developer.toLowerCase() === email;
}

/** Whether this sign-in carries a scope: a viewer holds only `read`, a member
 *  `operate` and `ingest`, the owner `admin` too. An open plane, or not known
 *  yet: yes — the plane still says no where it must. */
export function useHasScope(): (scope: "read" | "ingest" | "operate" | "admin") => boolean {
  const apiToken = useApp((s) => s.apiToken);
  const q = useQuery({ queryKey: ["auth-status", apiToken], queryFn: api.authStatus });
  return (scope) => !q.data || !q.data.auth_enabled || q.data.scopes.includes(scope);
}

/** Like useHasScope, but false until the plane has answered: for a request
 *  that must not go out unless the caller holds the scope (QA-121). */
export function useHasScopeKnown(): (scope: "read" | "ingest" | "operate" | "admin") => boolean {
  const apiToken = useApp((s) => s.apiToken);
  const q = useQuery({ queryKey: ["auth-status", apiToken], queryFn: api.authStatus });
  return (scope) => !!q.data && (!q.data.auth_enabled || q.data.scopes.includes(scope));
}

/** The scopes this sign-in holds; null on an open plane or while not known. */
export function useScopes(): string[] | null {
  const apiToken = useApp((s) => s.apiToken);
  const q = useQuery({ queryKey: ["auth-status", apiToken], queryFn: api.authStatus });
  return q.data && q.data.auth_enabled ? q.data.scopes : null;
}
