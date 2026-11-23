// Settings — the "loud elsewhere" companion to quiet-until-wrong (spec section
// 16) plus connection and license. Notifications emit-and-route (webhook), with
// the dead-letter log surfaced honestly; a notification system that fails
// silently is worse than none.
import { useEffect, useState } from "react";
import { track } from "../funnel";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Check, Copy, Loader2, Trash2 } from "lucide-react";
import { api, BillingReport, BillingStatement, type Capability, type Permissions } from "../api";
import { useCanAdmin } from "../team";
import {
  IdentityError, emailAvailable, forgotPassword, here, login, logout, oauthProviders,
  oauthStartUrl, signup, signupAvailable, ssoDiscover, ssoStartUrl, type OAuthProvider,
} from "../identity";
import { navigate } from "../router";
import { MODE_LABEL, useApp } from "../store";
import { C, MONO, btn } from "../theme";
import Coach from "../components/Coach";
import ToolCredentials from "../components/ToolCredentials";
import UsageAndBilling from "../components/UsageAndBilling";
import AuditTrail from "../components/AuditTrail";
import { useStartTour } from "../components/Tour";
import { errorWords } from "../refusal";

// Every trigger the backend emits, in words, and for whom. A trigger that fires
// and cannot be subscribed to is a notification nobody receives — which is how
// `license_expiring` and `behavioral_drift` sat here, emitted and unreachable.
type Journey = "coding" | "production" | "both";
const TRIGGERS: { id: string; label: string; for: Journey[] }[] = [
  { id: "evidence_run", label: "an agent said something its own run contradicts — a tool result it made up, tests it said passed",
    for: ["coding", "production", "both"] },
  { id: "dangerous_let_through", label: "a dangerous call went through — it deleted something, or ran, sent or wrote what outside text said",
    for: ["coding", "production", "both"] },
  { id: "would_refuse", label: "a call the rules would refuse went through, because enforcement is off (observe mode)",
    for: ["coding", "production", "both"] },
  { id: "memory_worse", label: "agents did worse after the team memory changed", for: ["coding", "both"] },
  { id: "setup_health_worse", label: "a context health check came out worse than the last one", for: ["coding", "both"] },
  { id: "level_transition_up", label: "an agent was restricted further (its level went up)", for: ["production", "both"] },
  { id: "behavioral_drift", label: "an agent's behaviour drifted (its health check)", for: ["production", "both"] },
  { id: "heat_threshold", label: "something an agent touched is getting suspicious across sessions (sentinel)", for: ["production", "both"] },
  { id: "node_stale", label: "an agent stopped reporting", for: ["production", "both"] },
  { id: "regression_failed", label: "a policy change breaks a case you kept (scheduled check)", for: ["production", "both"] },
  { id: "license_expiring", label: "the license is about to expire (30, 14, 7, 3, 1 days before)", for: ["coding", "production", "both"] },
];

const KEY_SCOPES = ["read", "ingest", "operate", "admin"];

const INPUT_STYLE = {
  background: C.bg, border: `1px solid ${C.line}`, borderRadius: 5, color: C.text,
  fontFamily: MONO, fontSize: 12, padding: "7px 9px", outline: "none", width: "100%",
} as const;

/** Sign in with the shared axor-identity service. On success the access token
 * becomes the bearer (renewed transparently on expiry via api.af); operators
 * who prefer a static token can still paste one below. */
// The signed-in human's organization: which orgs they belong to (from the
// identity service's /v1/me, which also proves the session is still live) and,
// for an admin, adding an existing user to the active org.
/** "Address not confirmed · resend" — shown only while it is not, and only
 * where the identity service sends mail. */
function EmailStatus() {
  const me = useQuery({ queryKey: ["identity-me"], queryFn: api.identityMe });
  const [canMail, setCanMail] = useState(false);
  useEffect(() => { void emailAvailable().then(setCanMail); }, []);
  const resend = useMutation({ mutationFn: () => api.resendVerification(here()) });
  if (!canMail || me.data?.user.email_verified !== false) return null;
  return (
    <div className="flex items-center gap-2 mb-2" data-testid="email-unverified"
      style={{ fontFamily: MONO, fontSize: 11, color: C.amber }}>
      e-mail not confirmed — alert mail goes only to a confirmed address
      {resend.isSuccess ? (
        <span style={{ color: C.green }}>· link sent to {resend.data.sent_to}</span>
      ) : (
        <button onClick={() => resend.mutate()} disabled={resend.isPending}
          style={btn({ color: C.mut, fontSize: 10.5, padding: "2px 8px" })}>
          {resend.isPending ? "…" : "resend link"}
        </button>
      )}
      {resend.isError && <span style={{ color: C.red }}>{(resend.error as Error).message}</span>}
    </div>
  );
}

function OrgMembers() {
  const me = useQuery({ queryKey: ["identity-me"], queryFn: api.identityMe });
  if (me.isPending) return null;
  if (me.isError) {
    return (
      <div className="mb-3" style={{ fontFamily: MONO, fontSize: 11, color: C.red }}>
        cannot read your organization: {(me.error as Error).message}
      </div>
    );
  }
  const d = me.data;
  const active = d.memberships.find((m) => m.org_id === d.active_org);
  const isAdmin = d.role === "admin" || d.role === "owner";
  return (
    <div className="mb-3" style={{ fontFamily: MONO, fontSize: 11, color: C.mut, display: "flex", flexDirection: "column", gap: 6 }}>
      <div>
        workspace <span style={{ color: C.text }}>{active?.name ?? d.active_org}</span>{" "}
        <span data-testid="workspace-id" title="a license is issued to this id: give it when you buy one"
          style={{ color: C.dim }}>({d.active_org})</span> · you are{" "}
        <span style={{ color: C.text }}>{d.role}</span>
        {d.memberships.length > 1 && (
          <span style={{ color: C.dim }}>
            {" "}· also in {d.memberships.filter((m) => m.org_id !== d.active_org).map((m) => m.name).join(", ")}
            {" "}(sign in with that workspace's id to switch)
          </span>
        )}
      </div>
      <Team orgId={d.active_org} isAdmin={isAdmin} />
      <WhoMay orgId={d.active_org} isAdmin={isAdmin} />
      {isAdmin && <SingleSignOn orgId={d.active_org} />}
    </div>
  );
}

/** A workspace's single sign-on (OpenID Connect): its provider, the client
 *  this service is registered as there, the domains it signs in — proved by an
 *  owner's or admin's own confirmed address — and whether it is the only way. */
function SingleSignOn({ orgId }: { orgId: string }) {
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["sso", orgId], queryFn: () => api.sso(orgId), retry: false });
  const [open, setOpen] = useState(false);
  const [form, setForm] = useState({ issuer: "", client_id: "", client_secret: "", domains: "",
                                     enforce: false, default_role: "member" });
  const [copied, setCopied] = useState(false);
  useEffect(() => {
    const d = q.data;
    if (d) setForm({ issuer: d.issuer ?? "", client_id: d.client_id ?? "", client_secret: "",
                     domains: (d.domains ?? d.provable_domains).join(", "), enforce: !!d.enforce,
                     default_role: d.default_role ?? "member" });
  }, [q.data]);
  const save = useMutation({
    mutationFn: () => api.setSso(orgId, {
      issuer: form.issuer.trim(), client_id: form.client_id.trim(),
      ...(form.client_secret ? { client_secret: form.client_secret } : {}),
      domains: form.domains.split(/[,\s]+/).filter(Boolean), enforce: form.enforce,
      default_role: form.default_role }),
    onSuccess: () => { setOpen(false); void qc.invalidateQueries({ queryKey: ["sso", orgId] }); },
  });
  const remove = useMutation({
    mutationFn: () => api.removeSso(orgId),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ["sso", orgId] }),
  });
  const d = q.data;
  if (!d) return null;
  const set = (k: keyof typeof form) => (e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement>) =>
    setForm((f) => ({ ...f, [k]: e.target.type === "checkbox" ? (e.target as HTMLInputElement).checked : e.target.value }));
  return (
    <div data-testid="sso" className="mt-3">
      <div style={{ color: C.dim, letterSpacing: "0.08em", fontSize: 10.5, marginBottom: 4 }}>SINGLE SIGN-ON</div>
      {!d.available ? (
        <div style={{ color: C.dim }}>
          needs this service&apos;s public address (AXOR_IDENTITY_PUBLIC_URL) — the provider sends people back to it
        </div>
      ) : d.configured && !open ? (
        <div data-testid="sso-on" style={{ lineHeight: 1.7 }}>
          <span style={{ color: C.green }}>● on</span> — {d.domains?.join(", ")} sign in through{" "}
          <span style={{ color: C.text }}>{d.issuer}</span>
          {d.enforce ? <span style={{ color: C.amber }}> · the only way in (the owner keeps a password)</span>
            : <span style={{ color: C.dim }}> · passwords still work</span>}
          <span style={{ color: C.dim }}> · newcomers join as {d.default_role}</span>
          <div className="flex gap-3">
            <button onClick={() => setOpen(true)} data-testid="sso-edit"
              style={{ background: "none", border: "none", padding: 0, cursor: "pointer", color: C.steel, fontFamily: MONO, fontSize: 11 }}>
              change
            </button>
            <button onClick={() => { if (window.confirm("Turn single sign-on off? People sign in with passwords again.")) remove.mutate(); }}
              style={{ background: "none", border: "none", padding: 0, cursor: "pointer", color: C.dim, fontFamily: MONO, fontSize: 11 }}>
              turn off
            </button>
          </div>
        </div>
      ) : !open && d.plan_ok === false ? (
        <div data-testid="sso-plan" style={{ color: C.dim }}>
          off — single sign-on is on the{" "}
          <a href="#/pricing?for=coding" style={{ color: C.steel }}>Business plan</a> ($40 per developer):
          Okta, Entra ID, Google Workspace, any OpenID provider
        </div>
      ) : !open ? (
        <div style={{ color: C.dim }}>
          off — people sign in with a password, GitHub or Google.{" "}
          <button onClick={() => setOpen(true)} data-testid="sso-setup"
            style={{ background: "none", border: "none", padding: 0, cursor: "pointer", color: C.steel, fontFamily: MONO, fontSize: 11 }}>
            set up SSO (Okta, Entra ID, Google Workspace, any OpenID provider)
          </button>
        </div>
      ) : (
        <form data-testid="sso-form" onSubmit={(e) => { e.preventDefault(); save.mutate(); }}
          className="flex flex-col" style={{ gap: 6 }}>
          <div style={{ color: C.mut, lineHeight: 1.6 }}>
            1. In your provider, register a web app with this sign-in redirect:{" "}
            <span data-testid="sso-callback" style={{ color: C.text }}>{d.callback_url}</span>{" "}
            <button type="button" onClick={() => { void navigator.clipboard?.writeText(d.callback_url); setCopied(true); }}
              style={{ background: "none", border: "none", padding: 0, cursor: "pointer", color: C.steel, fontFamily: MONO, fontSize: 11 }}>
              {copied ? "copied" : "copy"}
            </button>
            <br />2. Give its issuer, client id and secret here.
          </div>
          <input value={form.issuer} onChange={set("issuer")} placeholder="issuer — https://acme.okta.com"
            aria-label="issuer" style={INPUT_STYLE} data-testid="sso-issuer" />
          <input value={form.client_id} onChange={set("client_id")} placeholder="client id" aria-label="client id"
            style={INPUT_STYLE} data-testid="sso-client-id" />
          <input type="password" value={form.client_secret} onChange={set("client_secret")} aria-label="client secret"
            placeholder={d.has_secret ? "client secret — kept as it is if left empty" : "client secret"}
            style={INPUT_STYLE} data-testid="sso-secret" />
          <input value={form.domains} onChange={set("domains")} placeholder="domains — acme.com" aria-label="domains"
            style={INPUT_STYLE} data-testid="sso-domains" />
          <div style={{ color: C.dim, fontSize: 10.5 }}>
            a domain is yours to sign in if an owner or admin here confirmed their own address at it
            {d.provable_domains.length ? ` (${d.provable_domains.join(", ")})` : " — none yet: confirm your address first"}
          </div>
          <div className="flex items-center gap-4 flex-wrap">
            <label className="flex items-center gap-1" style={{ cursor: "pointer" }}>
              <input type="checkbox" checked={form.enforce} onChange={set("enforce")} data-testid="sso-enforce" />
              the only way in for these domains
            </label>
            <label className="flex items-center gap-1">
              newcomers join as
              <select value={form.default_role} onChange={set("default_role")} aria-label="default role"
                style={{ ...INPUT_STYLE, width: 100, padding: "1px 4px" }}>
                <option value="member">member</option>
                <option value="viewer">viewer</option>
              </select>
            </label>
          </div>
          {save.isError && <div data-testid="sso-error" style={{ color: C.red }}>{errorWords((save.error as Error).message)}</div>}
          <div className="flex gap-2">
            <button type="submit" disabled={save.isPending} data-testid="sso-save"
              style={btn({ color: C.text, borderColor: C.steel, fontSize: 11, padding: "4px 12px" })}>
              {save.isPending ? "checking the provider…" : "save"}
            </button>
            <button type="button" onClick={() => setOpen(false)}
              style={{ background: "none", border: "none", padding: 0, cursor: "pointer", color: C.dim, fontFamily: MONO, fontSize: 11 }}>
              cancel
            </button>
          </div>
        </form>
      )}
    </div>
  );
}

/** Who may do what to the team's agents beyond their own: members as a whole,
 *  or named people (a lead on call stops a teammate's runaway session without
 *  being made an admin). Owners and admins always may; viewers never. */
function WhoMay({ orgId, isAdmin }: { orgId: string; isAdmin: boolean }) {
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["permissions"], queryFn: api.permissions, retry: false });
  const members = useQuery({ queryKey: ["org-members", orgId], queryFn: () => api.orgMembers(orgId), retry: false });
  const save = useMutation({
    mutationFn: (change: Parameters<typeof api.setPermissions>[0]) => api.setPermissions(change),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ["permissions"] }),
  });
  if (!q.data) return null;
  const caps = Object.entries(q.data.capabilities) as [Capability, Permissions["capabilities"][Capability]][];
  // people who may be named: members (owners and admins already may, viewers never)
  const named = (members.data?.members ?? []).filter((m) => m.role === "member").map((m) => m.email);
  // changing who may is the Business plan's; what is set holds on any plan
  const canChange = isAdmin && q.data.editable !== false;
  return (
    <div data-testid="who-may" className="mt-3">
      <div style={{ color: C.dim, letterSpacing: "0.08em", fontSize: 10.5, marginBottom: 4 }}>WHO MAY</div>
      <div style={{ color: C.dim, fontSize: 10.5, marginBottom: 6, lineHeight: 1.6 }}>
        beyond their own agents — owners and admins always, viewers never
        {isAdmin && !canChange && (
          <span data-testid="who-may-plan"> · changing these is on the{" "}
            <a href="#/pricing?for=coding" style={{ color: C.steel }}>Business plan</a></span>
        )}
      </div>
      {caps.map(([cap, c]) => {
        const left = named.filter((e) => !c.people.includes(e));
        return (
          <div key={cap} data-testid={`may-${cap}`} className="flex gap-2 items-center flex-wrap"
            style={{ padding: "3px 0", borderTop: `1px solid ${C.line}` }}>
            <span style={{ color: C.text, flex: "1 1 260px" }}>{c.what}</span>
            <label className="flex items-center gap-1" style={{ cursor: isAdmin ? "pointer" : "default" }}>
              <input type="checkbox" checked={c.members} disabled={!canChange || save.isPending}
                aria-label={`members may ${c.what}`} data-testid={`may-${cap}-members`}
                onChange={(e) => save.mutate({ [cap]: { members: e.target.checked } })} />
              members
            </label>
            {c.people.map((p) => (
              <span key={p} style={{ color: C.steel, border: `1px solid ${C.line}`, borderRadius: 20, padding: "0 7px" }}>
                {p}
                {canChange && (
                  <button aria-label={`take ${cap} from ${p}`} disabled={save.isPending}
                    onClick={() => save.mutate({ [cap]: { people: c.people.filter((x) => x !== p) } })}
                    style={{ background: "none", border: "none", padding: "0 0 0 5px", cursor: "pointer", color: C.dim, fontFamily: MONO }}>
                    ×
                  </button>
                )}
              </span>
            ))}
            {canChange && !c.members && left.length > 0 && (
              <select value="" aria-label={`let someone ${c.what}`} data-testid={`may-${cap}-add`}
                onChange={(e) => e.target.value && save.mutate({ [cap]: { people: [...c.people, e.target.value] } })}
                style={{ ...INPUT_STYLE, width: 150, padding: "1px 4px" }}>
                <option value="">+ a person…</option>
                {left.map((e) => <option key={e} value={e}>{e}</option>)}
              </select>
            )}
            {!isAdmin && (
              <span style={{ color: q.data!.you[cap] ? C.green : C.dim }}>{q.data!.you[cap] ? "you may" : "not you"}</span>
            )}
          </div>
        );
      })}
      {save.isError && <div style={{ color: C.red }}>{errorWords((save.error as Error).message)}</div>}
    </div>
  );
}

const inviteUrl = (token: string) =>
  `${window.location.origin}${window.location.pathname}#/invite/${encodeURIComponent(token)}`;

/** Who is in the workspace, and — for an admin — inviting someone, account or
 * not: a single-use link for a role, mailed when an address is given and the
 * install sends mail. */
function Team({ orgId, isAdmin }: { orgId: string; isAdmin: boolean }) {
  const qc = useQueryClient();
  const members = useQuery({ queryKey: ["org-members", orgId], queryFn: () => api.orgMembers(orgId), retry: false });
  const invites = useQuery({ queryKey: ["org-invites", orgId], queryFn: () => api.orgInvites(orgId),
                             enabled: isAdmin, retry: false });
  const [email, setEmail] = useState("");
  const [role, setRole] = useState("member");
  const [made, setMade] = useState<{ link: string; mailed: boolean; email: string | null } | null>(null);
  const [copied, setCopied] = useState(false);
  // the free plan is one developer: a second one is the moment to offer the
  // paid plan, not a silent invite whose sessions then show up locked
  const ent = useQuery({ queryKey: ["entitlements"], queryFn: api.entitlements, retry: false });
  const developers = (members.data?.members ?? []).filter((m) => m.role !== "viewer").length
    + (invites.data?.invites ?? []).filter((i) => i.role !== "viewer").length;
  const needsSeat = ent.data?.free === true && ent.data.developers !== null
    && developers >= ent.data.developers && role !== "viewer";
  const [offer, setOffer] = useState(false);
  // on a paid plan: what a developer joining now would take — the next seat,
  // its price, or nothing past the workspace's seat limit
  const billing = useQuery({ queryKey: ["billing"], queryFn: api.billing, retry: false });
  const forecast = billing.data?.forecast ?? null;
  const seatNote = (() => {
    if (!forecast || role === "viewer" || ent.data?.free !== false) return null;
    const next = forecast.developers_so_far + 1;
    if (forecast.seat_cap !== null && (forecast.seats_left ?? 0) <= 0) {
      return { warn: true, text: `The seat limit (${forecast.seat_cap}) is used up this month: if they run coding agents, their sessions stay protected but are not shown — and not billed — until you raise it.` };
    }
    const included = billing.data?.plan.included_developers ?? 0;
    return { warn: false, text: next <= included
      ? `If they run coding agents this month, they take seat ${next} — within the ${included} your plan includes.`
      : `If they run coding agents this month, they take seat ${next}: +${billing.data?.plan.seat ?? "$25.00"} this month. Viewers are free.` };
  })();
  // the plans, beside this page: an invite or a role change half made stays here
  const openPricing = () => window.open(`${window.location.pathname}#/pricing?for=coding`, "_blank", "noopener");
  // a viewer made a member or an admin is a developer too: the same moment
  const [promote, setPromote] = useState<{ userId: string; email: string; to: string } | null>(null);
  const invite = useMutation({
    mutationFn: (as?: string) => api.createInvite(orgId, as ?? role, email.trim() || null, here()),
    onSuccess: (r) => {
      setMade({ link: inviteUrl(r.token), mailed: r.mailed, email: r.email });
      setCopied(false);
      setEmail("");
      void qc.invalidateQueries({ queryKey: ["org-invites", orgId] });
    },
  });
  const revoke = useMutation({
    mutationFn: (id: string) => api.revokeInvite(orgId, id),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ["org-invites", orgId] }),
  });
  const me = useApp((s) => s.identityEmail);
  // a viewer has no key and a removed teammate no access: their own keys go too
  const setRole2 = useMutation({
    mutationFn: async ({ userId, to }: { userId: string; to: string }) => {
      const r = await api.setMemberRole(orgId, userId, to);
      if (to === "viewer") await api.revokeKeysOf(userId);
      return r;
    },
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["org-members", orgId] });
      void qc.invalidateQueries({ queryKey: ["api-keys"] });
    },
  });
  const remove = useMutation({
    mutationFn: async (userId: string) => {
      await api.removeMember(orgId, userId);
      return api.revokeKeysOf(userId);
    },
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["org-members", orgId] });
      void qc.invalidateQueries({ queryKey: ["api-keys"] });
    },
  });
  return (
    <div data-testid="team" className="mt-2">
      <div style={{ color: C.dim, letterSpacing: "0.08em", fontSize: 10.5, marginBottom: 4 }}>TEAM</div>
      {(members.data?.members ?? []).map((m) => {
        // an admin sets anyone's role but their own and the owner's
        const editable = isAdmin && m.role !== "owner" && m.email !== me;
        return (
          <div key={m.user_id} data-testid={`member-${m.email}`} className="flex gap-3 items-center" style={{ padding: "2px 0" }}>
            <span style={{ color: C.text, flex: 1 }}>{m.email}</span>
            {editable ? (
              <>
                <select value={m.role} aria-label={`role of ${m.email}`} disabled={setRole2.isPending}
                  onChange={(e) => {
                    const to = e.target.value;
                    const seat = ent.data?.free === true && ent.data.developers !== null
                      && m.role === "viewer" && to !== "viewer" && developers >= ent.data.developers;
                    if (seat) setPromote({ userId: m.user_id, email: m.email, to });
                    else setRole2.mutate({ userId: m.user_id, to });
                  }}
                  style={{ ...INPUT_STYLE, width: 90, padding: "1px 4px" }}>
                  {["admin", "member", "viewer"].map((r) => <option key={r} value={r}>{r}</option>)}
                </select>
                <button aria-label={`remove ${m.email}`} disabled={remove.isPending}
                  onClick={() => { if (window.confirm(`Take ${m.email} off this workspace? Their keys are revoked too.`)) remove.mutate(m.user_id); }}
                  style={{ background: "none", border: "none", padding: 0, cursor: "pointer", color: C.dim, fontFamily: MONO, fontSize: 11 }}>
                  remove
                </button>
              </>
            ) : <span>{m.role}</span>}
          </div>
        );
      })}
      {promote && (
        <div data-testid="promote-upgrade" className="mt-2 p-3" style={{ background: C.panel2, border: `1px solid ${C.amber}`, borderRadius: 6 }}>
          <div style={{ color: C.text, marginBottom: 4 }}>
            As a {promote.to}, {promote.email} is a second developer — that is the Coding plan.
          </div>
          <div style={{ color: C.mut, marginBottom: 8 }}>
            $25 per developer per month — every developer's agents governed, shared skills and team memory.
          </div>
          <div className="flex gap-3 items-center flex-wrap">
            <button type="button" data-testid="promote-upgrade-go" onClick={openPricing}
              style={btn({ color: C.text, borderColor: C.amber, fontSize: 11, padding: "4px 12px" })}>
              see the plan →
            </button>
            <button type="button" data-testid="promote-keep" onClick={() => setPromote(null)}
              style={{ background: "none", border: "none", padding: 0, cursor: "pointer", color: C.steel, fontFamily: MONO, fontSize: 11 }}>
              keep them a viewer
            </button>
          </div>
        </div>
      )}
      {(setRole2.isError || remove.isError) && (
        <div style={{ color: C.red, fontSize: 11 }}>{((setRole2.error ?? remove.error) as Error).message}</div>
      )}
      {isAdmin && (
        <>
          <form onSubmit={(e) => {
            e.preventDefault();
            if (needsSeat) { setOffer(true); return; }
            if (!invite.isPending) invite.mutate(undefined);
          }}
            className="flex items-center gap-2 mt-2">
            <input type="email" value={email} placeholder="their e-mail (optional — or send the link yourself)"
              onChange={(e) => setEmail(e.target.value)} aria-label="invite email"
              style={{ ...INPUT_STYLE, flex: 1 }} />
            <select value={role} onChange={(e) => setRole(e.target.value)} aria-label="invite role"
              style={{ ...INPUT_STYLE, width: 100 }}>
              {["member", "admin", "viewer"].map((r) => <option key={r} value={r}>{r}</option>)}
            </select>
            <button type="submit" disabled={invite.isPending} data-testid="invite-make"
              style={btn({ color: C.text, borderColor: C.steel, fontSize: 11, padding: "4px 10px" })}>
              {invite.isPending ? "…" : "invite"}
            </button>
          </form>
          {seatNote && (
            <div data-testid="invite-seat-note" className="mt-1"
              style={{ fontSize: 10.5, color: seatNote.warn ? C.amber : C.dim, lineHeight: 1.6 }}>
              {seatNote.text}
              {seatNote.warn && (
                <button type="button" onClick={() => navigate("settings", { tab: "billing" })}
                  style={{ background: "none", border: "none", padding: "0 0 0 6px", cursor: "pointer", color: C.steel, fontFamily: MONO, fontSize: 10.5 }}>
                  seat limit →
                </button>
              )}
            </div>
          )}
          {offer && needsSeat && (
            <div data-testid="invite-upgrade" className="mt-2 p-3" style={{ background: C.panel2, border: `1px solid ${C.amber}`, borderRadius: 6 }}>
              <div style={{ color: C.text, marginBottom: 4 }}>A second developer is on the Coding plan.</div>
              <div style={{ color: C.mut, marginBottom: 8 }}>
                $25 per developer per month — every developer's agents governed, shared skills and team memory.
              </div>
              <div className="flex gap-3 items-center flex-wrap">
                <button type="button" data-testid="invite-upgrade-go" onClick={openPricing}
                  style={btn({ color: C.text, borderColor: C.amber, fontSize: 11, padding: "4px 12px" })}>
                  see the plan →
                </button>
                <button type="button" data-testid="invite-as-viewer"
                  onClick={() => { setRole("viewer"); setOffer(false); invite.mutate("viewer"); }}
                  style={{ background: "none", border: "none", padding: 0, cursor: "pointer", color: C.steel, fontFamily: MONO, fontSize: 11 }}>
                  or invite them as a viewer — free, they watch
                </button>
              </div>
            </div>
          )}
          {made && (
            <div data-testid="invite-made" className="mt-1 p-2" style={{ background: C.panel2, border: `1px solid ${C.line}`, borderRadius: 5 }}>
              <div style={{ color: C.mut, marginBottom: 4 }}>
                {made.mailed ? `mailed to ${made.email} — or send this link:` : "send this link — it works once, for seven days:"}
                <button type="button" onClick={() => { void navigator.clipboard?.writeText(made.link); setCopied(true); }}
                  style={{ background: "none", border: "none", padding: "0 0 0 8px", cursor: "pointer", color: C.steel, fontFamily: MONO, fontSize: 11 }}>
                  {copied ? "copied" : "copy"}
                </button>
              </div>
              <div style={{ color: C.green, wordBreak: "break-all" }}>{made.link}</div>
            </div>
          )}
          {invite.isError && <span style={{ color: C.red }}>{(invite.error as Error).message}</span>}
          {(invites.data?.invites ?? []).length > 0 && (
            <div className="mt-2" data-testid="invites-open">
              <div style={{ color: C.dim, fontSize: 10.5 }}>waiting to be accepted</div>
              {(invites.data?.invites ?? []).map((i) => (
                <div key={i.invite_id} className="flex gap-3" style={{ padding: "2px 0" }}>
                  <span style={{ flex: 1 }}>{i.email ?? "anyone with the link"} · {i.role}</span>
                  <span style={{ color: C.dim }}>until {i.expires_ts.slice(0, 10)}</span>
                  <button type="button" onClick={() => revoke.mutate(i.invite_id)}
                    style={{ background: "none", border: "none", padding: 0, cursor: "pointer", color: C.mut, fontFamily: MONO, fontSize: 11 }}>
                    withdraw
                  </button>
                </div>
              ))}
            </div>
          )}
        </>
      )}
    </div>
  );
}

export function IdentityLogin({ onDone, initialMode = "login" }: {
  onDone?: () => void; initialMode?: "login" | "signup";
} = {}) {
  const identityEmail = useApp((s) => s.identityEmail);
  const setSession = useApp((s) => s.setSession);
  const clearSession = useApp((s) => s.clearSession);
  const qc = useQueryClient();
  const [mode, setMode] = useState<"login" | "signup" | "forgot">(initialMode);
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [orgName, setOrgName] = useState("");
  const [orgId, setOrgId] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [canSignup, setCanSignup] = useState<boolean | null>(null);
  const [providers, setProviders] = useState<OAuthProvider[]>([]);
  const [canMail, setCanMail] = useState(false);
  const [sent, setSent] = useState<string | null>(null);
  // a work address whose workspace signs in with single sign-on
  const [ssoFor, setSsoFor] = useState<{ workspace: string; enforce: boolean } | null>(null);
  const checkSso = async (address: string) => {
    if (!address.includes("@")) { setSsoFor(null); return; }
    const d = await ssoDiscover(address.trim());
    setSsoFor(d.sso ? { workspace: d.workspace ?? "", enforce: !!d.enforce } : null);
  };
  const startSso = async () => {
    if (!email.includes("@")) { setError("type your work e-mail first — it says which workspace"); return; }
    const d = await ssoDiscover(email.trim());
    if (!d.sso) { setError("no single sign-on for that address — sign in with your password"); return; }
    window.location.href = ssoStartUrl(email.trim());
  };
  useEffect(() => {
    void signupAvailable().then(setCanSignup);
    void oauthProviders().then(setProviders);
    void emailAvailable().then(setCanMail);
  }, []);

  if (identityEmail) {
    return (
      <>
      <div className="flex items-center gap-2 mb-3" style={{ fontFamily: MONO, fontSize: 11.5 }}>
        <span style={{ color: C.green }}>● signed in as {identityEmail}</span>
        <button
          onClick={() => {
            void logout(useApp.getState().refreshToken);
            clearSession();
            // Drop what this session could read so it does not linger on screen.
            qc.clear();
            onDone?.();
          }}
          style={btn({ color: C.mut, fontSize: 10.5, padding: "3px 10px" })}>
          log out
        </button>
      </div>
      <EmailStatus />
      <OrgMembers />
      </>
    );
  }

  async function submit() {
    setBusy(true);
    setError(null);
    if (mode === "forgot") {
      try {
        setSent((await forgotPassword(email)).detail);
      } catch (err) {
        setError(err instanceof IdentityError ? err.message : "could not send the link");
      } finally {
        setBusy(false);
      }
      return;
    }
    try {
      const session =
        mode === "signup"
          ? await signup(email, password, orgName)
          : await login(email, password, orgId || undefined);
      setSession(session.access_token, session.refresh_token, session.user.email);
      if (mode === "signup") track("signup_completed");
      setPassword("");
      // Queries that 401'd before sign-in stay failed otherwise.
      void qc.invalidateQueries();
      onDone?.();
    } catch (err) {
      setError(err instanceof IdentityError ? err.message : "login failed");
      if (err instanceof IdentityError && err.code === "sso_required") void checkSso(email);
    } finally {
      setBusy(false);
    }
  }

  const canSubmit = mode === "forgot" ? email : email && password && (mode === "login" || orgName);

  return (
    <form
      onSubmit={(e) => {
        e.preventDefault();
        if (canSubmit && !busy) submit();
      }}
      className="mb-3"
      style={{ display: "flex", flexDirection: "column", gap: 8 }}
    >
      <div style={{ fontFamily: MONO, fontSize: 10.5, color: C.dim, letterSpacing: "0.08em" }}>
        {mode === "signup" ? "CREATE YOUR WORKSPACE" : mode === "forgot" ? "RESET YOUR PASSWORD" : "SIGN IN"}
      </div>
      {providers.length > 0 && (
        <>
          <div className="flex items-center gap-2" style={{ flexWrap: "wrap" }}>
            {providers.map((p) => (
              <a key={p.name} href={oauthStartUrl(p.name)} data-testid={`oauth-${p.name}`}
                style={btn({ color: C.text, borderColor: C.line, fontSize: 11, padding: "6px 14px", textDecoration: "none" })}>
                continue with {p.label}
              </a>
            ))}
          </div>
          <div style={{ fontFamily: MONO, fontSize: 10, color: C.dim }}>or with e-mail</div>
        </>
      )}
      <input type="email" autoComplete="email" value={email} placeholder="email"
        onChange={(e) => setEmail(e.target.value)} onBlur={() => { if (mode === "login") void checkSso(email); }}
        style={INPUT_STYLE} aria-label="email" />
      {mode === "login" && ssoFor && (
        <div data-testid="sso-offer" className="flex items-center gap-2 flex-wrap"
          style={{ fontFamily: MONO, fontSize: 11, color: C.mut }}>
          <span>{ssoFor.workspace ? `${ssoFor.workspace} signs in` : "your workspace signs in"} with single sign-on</span>
          <button type="button" data-testid="sso-continue" onClick={() => void startSso()}
            style={btn({ color: C.text, borderColor: C.steel, fontSize: 11, padding: "4px 12px" })}>
            continue with SSO →
          </button>
        </div>
      )}
      {mode !== "forgot" && (
      <input type="password" value={password} placeholder="password"
        autoComplete={mode === "signup" ? "new-password" : "current-password"}
        onChange={(e) => setPassword(e.target.value)} style={INPUT_STYLE} aria-label="password" />
      )}
      {mode === "forgot" ? (
        sent && <div data-testid="reset-sent" style={{ fontFamily: MONO, fontSize: 11, color: C.green }}>{sent}</div>
      ) : mode === "signup" ? (
        <input value={orgName} placeholder="organization name"
          onChange={(e) => setOrgName(e.target.value)} style={INPUT_STYLE} aria-label="organization name" />
      ) : (
        <input value={orgId} placeholder="organization id (optional)"
          onChange={(e) => setOrgId(e.target.value)} style={INPUT_STYLE} aria-label="organization id" />
      )}
      {error && <div style={{ fontFamily: MONO, fontSize: 11, color: C.red }}>{error}</div>}
      <div className="flex items-center gap-2">
        <button type="submit" disabled={!canSubmit || busy}
          style={btn({ color: C.steel, borderColor: C.steel, fontSize: 11, padding: "5px 14px" })}>
          {busy ? "…" : mode === "signup" ? "create workspace" : mode === "forgot" ? "send reset link" : "log in"}
        </button>
        {mode === "login" && !ssoFor && (
          <button type="button" data-testid="sso-start" onClick={() => void startSso()}
            style={btn({ color: C.dim, fontSize: 10.5, padding: "5px 10px" })}>
            sign in with SSO
          </button>
        )}
        {mode === "login" && canMail && (
          <button type="button" onClick={() => { setError(null); setSent(null); setMode("forgot"); }}
            style={btn({ color: C.dim, fontSize: 10.5, padding: "5px 10px" })}>
            forgot password?
          </button>
        )}
        {mode === "forgot" && (
          <button type="button" onClick={() => { setError(null); setMode("login"); }}
            style={btn({ color: C.dim, fontSize: 10.5, padding: "5px 10px" })}>
            back to log in
          </button>
        )}
        {mode !== "forgot" && (canSignup !== false || mode === "signup") && (
          <button type="button" onClick={() => { setError(null); setMode(mode === "signup" ? "login" : "signup"); }}
            style={btn({ color: C.dim, fontSize: 10.5, padding: "5px 10px" })}>
            {mode === "signup" ? "have an account? log in" : "new? create a workspace"}
          </button>
        )}
      </div>
    </form>
  );
}

export default function Settings({ tab = "workspace" }: { tab?: string } = {}) {
  const { mode, testBench } = useApp((s) => s.connection);
  const setTestBench = useApp((s) => s.setTestBench);
  const disconnect = useApp((s) => s.disconnect);
  const apiToken = useApp((s) => s.apiToken);
  const qc = useQueryClient();

  const [url, setUrl] = useState("");
  const identityEmail = useApp((s) => s.identityEmail);
  const [selected, setSelected] = useState<string[]>(["level_transition_up", "evidence_run"]);
  const [chanLabel, setChanLabel] = useState("");
  const [nodePattern, setNodePattern] = useState("");
  const [licenseJson, setLicenseJson] = useState("");

  const deadLetters = useQuery({ queryKey: ["dead-letters"], queryFn: api.deadLetters });
  const authStatus = useQuery({ queryKey: ["auth-status", apiToken], queryFn: api.authStatus });
  const licenseStatus = useQuery({ queryKey: ["license-status"], queryFn: api.licenseStatus });
  const eeActive = licenseStatus.data?.active === true;
  // undefined while the status query is in flight — the button stays disabled
  // rather than flashing enabled and then failing.
  const vendorKeyPinned = licenseStatus.data?.vendor_key_configured;
  const subscriptions = useQuery({ queryKey: ["subscriptions"], queryFn: api.listSubscriptions });
  // alert mail goes only where the identity service sends mail, and only to a
  // confirmed address: the button is offered when it can work
  const [canMail, setCanMail] = useState(false);
  useEffect(() => { void emailAvailable().then(setCanMail); }, []);
  const meQ = useQuery({ queryKey: ["identity-me"], queryFn: api.identityMe, enabled: !!identityEmail, retry: false });
  const verified = meQ.data?.user.email_verified !== false;

  const journey = useApp((s) => s.journey);
  const menuAll = useApp((s) => s.menuAll);
  const shownTriggers = TRIGGERS.filter((t) => (!journey || menuAll || t.for.includes(journey))
    // a license that expires is a licensed workspace's news
    && (t.id !== "license_expiring" || eeActive));
  const subscribe = useMutation({
    mutationFn: () =>
      // only what the list shows: a default this journey hides is not asked for
      api.subscribeNotifications(url, selected.filter((id) => shownTriggers.some((t) => t.id === id)), 0, {
        label: chanLabel || undefined,
        nodePattern: nodePattern || undefined,
      }),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ["subscriptions"] }),
  });
  const unsubscribe = useMutation({
    mutationFn: (target: string) => api.unsubscribeNotifications(target),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ["subscriptions"] }),
  });
  // A 200 means the license is verified AND active, so everything that renders
  // an entitlement has to be refetched — otherwise the panel says "active" while
  // Regression still shows its EE controls locked.
  const license = useMutation({
    mutationFn: async () => {
      const r = await api.verifyLicense(licenseJson);
      // the identity service learns the plan too (single sign-on is the
      // Business plan's): the same signed file, checked there
      const me = useApp.getState().identityEmail ? await api.identityMe().catch(() => null) : null;
      if (me?.active_org) await api.identityLicense(me.active_org, licenseJson).catch(() => null);
      return r;
    },
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["license-status"] });
      // the plan just changed: what it allows (seats, families, memory) with it
      void qc.invalidateQueries({ queryKey: ["entitlements"] });
      void qc.invalidateQueries({ queryKey: ["billing"] });
      void qc.invalidateQueries({ queryKey: ["regression-schedule"] });
      void qc.invalidateQueries({ queryKey: ["regression-history"] });
    },
  });

  const toggle = (id: string) =>
    setSelected((s) => (s.includes(id) ? s.filter((x) => x !== id) : [...s, id]));

  const learnMode = useApp((s) => s.learnMode);
  const setLearnMode = useApp((s) => s.setLearnMode);
  const coachDismissed = useApp((s) => s.coachDismissed);
  const resetCoach = useApp((s) => s.resetCoach);
  const startTour = useStartTour();

  const funnel = useQuery({ queryKey: ["funnel", 30], queryFn: () => api.funnelReport(30), retry: false });
  const canAdmin = useCanAdmin();
  const prod = !journey || menuAll || journey !== "coding";
  const coding = !journey || menuAll || journey !== "production";
  const tabs: [SettingsTab, string][] = [
    ["workspace", "workspace"], ["billing", "billing"], ["connect", "connect agents"], ["alerts", "alerts"],
    ...(prod ? [["security", "security"]] as [SettingsTab, string][] : []),
    ...(canAdmin ? [["audit", "audit"]] as [SettingsTab, string][] : []),
    ["learn", "learn"],
    ...(funnel.data ? [["operator", "operator"]] as [SettingsTab, string][] : []),
  ];
  const active: SettingsTab = tabs.some(([t]) => t === tab) ? tab as SettingsTab : "workspace";

  return (
    <div style={{ maxWidth: 680, margin: "0 auto" }}>
      <Coach id="settings" title="Settings — your workspace, your agents' keys, alerts">
        Workspace: who you are, your team, your plan. Connect agents: the keys your agents report with.
        Alerts: where you hear about it when something goes wrong.
      </Coach>
      <div className="flex items-center justify-between flex-wrap" style={{ gap: 8, margin: "0 0 14px" }}>
        <h1 style={{ fontSize: 22, fontWeight: 650, margin: 0 }}>Settings</h1>
        <button onClick={() => navigate("pricing")}
          style={{ background: "none", border: "none", color: C.steel, fontFamily: MONO, fontSize: 11.5, cursor: "pointer", padding: 0 }}>
          plans &amp; pricing →
        </button>
      </div>
      <div role="tablist" data-testid="settings-tabs" className="flex flex-wrap" style={{ gap: 4, marginBottom: 18, borderBottom: `1px solid ${C.line}` }}>
        {tabs.map(([id, label]) => (
          <button key={id} role="tab" aria-selected={active === id} data-testid={`settings-tab-${id}`}
            onClick={() => navigate("settings", { tab: id })}
            style={{ background: "none", border: "none", cursor: "pointer", fontFamily: MONO, fontSize: 12,
                     padding: "6px 10px", color: active === id ? C.text : C.dim,
                     borderBottom: `2px solid ${active === id ? C.steel : "transparent"}`, marginBottom: -1 }}>
            {label}
          </button>
        ))}
      </div>

      {active === "workspace" && (
        <>
          <Section title="ACCOUNT">
            <IdentityLogin />
            {authStatus.data && !authStatus.data.auth_enabled ? (
              <div style={{ fontFamily: MONO, fontSize: 11.5, color: C.mut, lineHeight: 1.6 }}>
                This plane is open — no sign-in needed. That is how a plane you run on your own machine
                starts; set <span style={{ color: C.text }}>AXOR_API_TOKEN</span> on the backend to require one.
              </div>
            ) : (
              <TokenFold />
            )}
          </Section>
          {/* a guest has no license to paste; a signed-in workspace does — the
              file the vendor sent after an upgrade is applied here */}
          {(!authStatus.data?.guest || licenseStatus.data?.active) && authStatus.data?.authenticated !== false
            && <Section title="LICENSE · the file you were sent">
            <details data-testid="license-fold" open={!!licenseStatus.data?.active || !!licenseStatus.data?.expired || undefined}>
              <summary style={{ cursor: "pointer", fontFamily: MONO, fontSize: 11.5, color: C.mut }}>
                {licenseStatus.data?.active
                  ? `active · ${licenseStatus.data.workspace_tier} · expires ${licenseStatus.data.expires_at}`
                  : licenseStatus.data?.expired ? "expired — open to paste a new one" : "no license — open to paste one"}
              </summary>
              <div className="mt-3">
        <div style={{ fontFamily: MONO, fontSize: 10.5, color: C.dim, marginBottom: 10 }}>
          Paste the license file you were sent after an upgrade (it is issued to your workspace id, shown
          above). It is checked
          here, offline — nothing calls us. When it expires the paid features turn read-only;
          what gets blocked never depends on a license.
        </div>
        {/* The ACTIVE license, from /v1/license/status. The panel used to show
            only the response to a paste, so a deployment that had been licensed
            for months showed nothing until somebody pasted again — and the
            fields with a deadline attached (days left, expired, whether this
            deployment renews itself at all) were not rendered anywhere. */}
        {licenseStatus.data && (
          <div className="mb-3" style={{ fontFamily: MONO, fontSize: 11 }}>
            {licenseStatus.data.active ? (
              <div style={{ color: (licenseStatus.data.days_remaining ?? 99) <= 14 ? C.amber : C.green }}>
                active · {licenseStatus.data.organization} ·{" "}
                {licenseStatus.data.workspace_tier} · expires{" "}
                {licenseStatus.data.expires_at}
                {typeof licenseStatus.data.days_remaining === "number" && (
                  <> · {licenseStatus.data.days_remaining} days left</>
                )}
              </div>
            ) : licenseStatus.data.expired ? (
              <div style={{ color: C.red }}>
                EXPIRED on {licenseStatus.data.expires_at} — EE surfaces are
                read-only. Safety is not gated and never was.
              </div>
            ) : (
              <div style={{ color: C.dim }}>no license active on this deployment</div>
            )}
            <div style={{ color: C.dim, fontSize: 10, marginTop: 3 }}>
              {licenseStatus.data.auto_renewal
                ? "auto-renewal configured (AXOR_LICENSE_RENEWAL_URL)"
                : "no auto-renewal — a new license has to be pasted here each term"}
              {" · "}
              {licenseStatus.data.usage_reporting
                ? "usage reported on the renewal channel"
                : "usage not reported"}
            </div>
            {licenseStatus.data.vendor_key_configured && !licenseStatus.data.licensed_to && (
              <div style={{ color: C.amber, fontSize: 10, marginTop: 3 }}>
                AXOR_ORG is unset, so ANY vendor-signed license activates here —
                including one issued to another customer. Set it to the name on
                your license.
              </div>
            )}
          </div>
        )}
        <textarea
          value={licenseJson}
          onChange={(e) => setLicenseJson(e.target.value)}
          rows={3}
          placeholder='{"license": {...}, "sig": "…"}'
          className="w-full mb-2"
          style={{ background: C.panel2, border: `1px solid ${C.line}`, borderRadius: 5, color: C.text, fontFamily: MONO, fontSize: 11, padding: 8, resize: "vertical", outline: "none" }}
        />
        {vendorKeyPinned === false && (
          <div className="mb-3" style={{ fontFamily: MONO, fontSize: 11, color: C.amber }}>
            This deployment pins no vendor public key, so a license signature
            cannot be checked against anything. Set <code>AXOR_VENDOR_PUBKEY</code>{" "}
            (see <code>.env.example</code>) and restart. The key ships with the
            distribution — it is deliberately not something this screen accepts,
            because a signature checked against a key typed in beside it proves
            nothing.
          </div>
        )}
        <button
          onClick={() => license.mutate()}
          disabled={!licenseJson || vendorKeyPinned !== true || license.isPending}
          style={btn({ color: C.steel, fontSize: 12 })}
        >
          {license.isPending ? <Loader2 size={13} className="animate-spin" /> : null} Verify license
        </button>
        {license.isError && (
          <div className="mt-2" style={{ fontFamily: MONO, fontSize: 11, color: C.red }}>
            {(license.error as Error).message}
          </div>
        )}
        {license.data && (
          <>
            <div className="mt-2" style={{ fontFamily: MONO, fontSize: 11, color: C.green }}>
              active · {license.data.organization} · {license.data.workspace_tier} workspace ·{" "}
              {/* One ladder: a rung entitles the Private Lab and the Control
                  Plane alike, so there are no module flags left to render —
                  only how many nodes the rung was sold with. */}
              {`up to ${license.data.governed_node_ceiling} governed nodes`}
              {license.data.self_hosted_runner ? " · self-hosted" : ""} · expires{" "}
              {license.data.expires_at} · {license.data.features.join(", ") || "no EE features"}
            </div>
            {license.data.over_ceiling && (
              <div className="mt-1" style={{ fontFamily: MONO, fontSize: 11, color: C.amber }}>
                {license.data.peak_nodes} peak nodes exceed the licensed ceiling of{" "}
                {license.data.governed_node_ceiling} — a warning, never a block (safety never
                checks a license). Contact us to raise the ceiling.
              </div>
            )}
          </>
        )}
                    </div>
            </details>
          </Section>}
        </>
      )}

      {active === "billing" && (
        <>
          <Billing coding={coding} prod={prod} />
          {!licenseStatus.data?.active && !authStatus.data?.guest && (
            <div data-testid="billing-license-hint" className="mb-4" style={{ fontFamily: MONO, fontSize: 11, color: C.mut }}>
              bought a plan? the license file you were sent goes under{" "}
              <button onClick={() => navigate("settings", { tab: "workspace" })}
                style={{ background: "none", border: "none", padding: 0, cursor: "pointer", color: C.steel, fontFamily: MONO, fontSize: 11 }}>
                workspace → license
              </button>
            </div>
          )}
          {coding && (
            <Section title="DEVELOPERS PER MONTH · coding agents">
              <DeveloperUsage />
            </Section>
          )}
          {prod && (
            <Section title="GOVERNED NODES PER MONTH · production agents">
              <UsageAndBilling />
            </Section>
          )}
        </>
      )}

      {active === "connect" && (
        <>
          <Section title="KEYS FOR YOUR AGENTS">
            <AgentKeys journey={journey} menuAll={menuAll} />
          </Section>
          <Section title="WHAT IS REPORTING">
            <Connections coding={coding} prod={prod} />
          </Section>
          {prod && (
            <Section title="THIS BROWSER'S CONNECTION">
              <div style={{ fontFamily: MONO, fontSize: 12, color: C.text, marginBottom: 8 }}>
                {MODE_LABEL[mode]}
              </div>
              <label className="flex items-center gap-2" style={{ fontFamily: MONO, fontSize: 11.5, color: C.mut, cursor: "pointer" }}>
                <input type="checkbox" checked={testBench} onChange={(e) => setTestBench(e.target.checked)} />
                a test bench — allow test interventions on it (inject a fault, self-heal)
              </label>
              {mode !== "none" && (
                <button onClick={disconnect} className="mt-3" style={btn({ color: C.mut, fontSize: 11 })}>
                  disconnect
                </button>
              )}
            </Section>
          )}
        </>
      )}

      {active === "alerts" && (
        <Section title="ALERTS · WEBHOOK OR E-MAIL">
        <div style={{ fontFamily: MONO, fontSize: 10.5, color: C.dim, marginBottom: 10 }}>
          Where you hear about it when something goes wrong: a webhook (JSON POST — Slack,
          Discord or PagerDuty take one) or an e-mail address. The screens stay quiet; this is
          where it gets loud.
        </div>
        <EmailStatus />
        {identityEmail && canMail && verified && (
          <button data-testid="mail-me"
            onClick={() => {
              setUrl(`mailto:${identityEmail}`);
              setSelected((cur) => (cur.includes("behavioral_drift") ? cur : [...cur, "behavioral_drift"]));
              subscribe.reset();
            }}
            className="mb-2"
            style={btn({ color: C.steel, borderColor: C.steel, fontSize: 10.5, padding: "3px 10px" })}>
            e-mail me alerts ({identityEmail})
          </button>
        )}
        <input
          value={url}
          onChange={(e) => { setUrl(e.target.value); subscribe.reset(); }}
          placeholder={canMail ? "https://hooks.example/… (JSON POST) or mailto:you@…"
            : "https://hooks.example/… — a webhook (JSON POST): Slack, Discord, PagerDuty"}
          className="w-full mb-3"
          style={{ background: C.bg, border: `1px solid ${C.line}`, borderRadius: 5, color: C.text, fontFamily: MONO, fontSize: 12, padding: "7px 9px", outline: "none" }}
        />
        <div className="flex flex-col gap-2 mb-3">
          {shownTriggers.map((t) => (
            <label key={t.id} className="flex items-center gap-2" style={{ fontFamily: MONO, fontSize: 11.5, color: C.mut, cursor: "pointer" }}>
              <input type="checkbox" checked={selected.includes(t.id)} onChange={() => toggle(t.id)} />
              {t.label}
            </label>
          ))}
        </div>
        {/* Routing (EE): named channel + node glob — the org layer. Inputs
            stay visible but disabled without a license (honest upsell). */}
        <div className="flex items-center gap-2 mb-3 flex-wrap" style={{ opacity: eeActive ? 1 : 0.55 }}>
          <input
            value={chanLabel}
            onChange={(e) => setChanLabel(e.target.value)}
            placeholder="channel label (e.g. team-a)"
            disabled={!eeActive}
            style={{ background: C.bg, border: `1px solid ${C.line}`, borderRadius: 5, color: C.text, fontFamily: MONO, fontSize: 11, padding: "5px 8px", width: 170, outline: "none" }}
          />
          <input
            value={nodePattern}
            onChange={(e) => setNodePattern(e.target.value)}
            placeholder="node pattern (e.g. team-a-*)"
            disabled={!eeActive}
            style={{ background: C.bg, border: `1px solid ${C.line}`, borderRadius: 5, color: C.text, fontFamily: MONO, fontSize: 11, padding: "5px 8px", width: 170, outline: "none" }}
          />
          {!eeActive && (
            <span style={{ fontFamily: MONO, fontSize: 10, color: C.dim }}>
              a channel per team and per agent group is on a paid plan — one channel for everything is free
            </span>
          )}
        </div>
        <button
          onClick={() => subscribe.mutate()}
          disabled={!url || selected.length === 0 || subscribe.isPending}
          style={btn({ color: C.steel, fontSize: 12 })}
        >
          {subscribe.isPending ? <Loader2 size={13} className="animate-spin" /> : subscribe.isSuccess ? <Check size={13} /> : null}
          {subscribe.isSuccess ? "subscribed" : "Subscribe"}
        </button>
        {subscribe.isError && (
          <div className="mt-2" style={{ fontFamily: MONO, fontSize: 11, color: C.red }}>
            {errorWords((subscribe.error as Error).message)}
          </div>
        )}

        {(subscriptions.data ?? []).length > 0 && (
          <>
            <div className="mt-4" style={{ fontFamily: MONO, fontSize: 10.5, color: C.dim, letterSpacing: "0.08em" }}>
              ACTIVE SUBSCRIPTIONS
            </div>
            {(subscriptions.data ?? []).map((s, i) => (
              <div key={i} className="flex items-center gap-2" style={{ fontFamily: MONO, fontSize: 11, color: C.mut, marginTop: 4 }}>
                <span>
                  {s.label ? `[${s.label}] ` : ""}{s.url} · {s.triggers
                    .map((id) => TRIGGERS.find((t) => t.id === id)?.label.split(" — ")[0] ?? id).join("; ")}
                  {s.node_pattern !== "*" ? ` · nodes: ${s.node_pattern}` : ""}
                </span>
                <button
                  aria-label={`unsubscribe ${s.url}`}
                  onClick={() => unsubscribe.mutate(s.url)}
                  disabled={unsubscribe.isPending}
                  style={btn({ color: C.mut, fontSize: 10, padding: "1px 7px" })}
                >
                  unsubscribe
                </button>
              </div>
            ))}
            {unsubscribe.isError && (
              <div className="mt-2" style={{ fontFamily: MONO, fontSize: 11, color: C.red }}>
                unsubscribe failed: {(unsubscribe.error as Error).message}
              </div>
            )}
          </>
        )}

        <div className="mt-4" style={{ fontFamily: MONO, fontSize: 10.5, color: C.dim, letterSpacing: "0.08em" }}>
          ALERTS THAT COULD NOT BE DELIVERED
        </div>
        {(deadLetters.data ?? []).length === 0 ? (
          <div style={{ fontFamily: MONO, fontSize: 11, color: C.dim, marginTop: 4 }}>
            none — every alert was delivered (each is retried before it lands here)
          </div>
        ) : (
          (deadLetters.data ?? []).map((d, i) => (
            <div key={i} style={{ fontFamily: MONO, fontSize: 11, color: C.amber, marginTop: 4 }}>
              {d.trigger} → {d.url} · {d.error} ({d.attempts} attempts)
              {d.created_ts ? ` · ${d.created_ts.slice(0, 19)}` : ""}
            </div>
          ))
        )}
      </Section>
      )}

      {active === "security" && (
        <>
          <Section title="HOW THIS PLANE IS PROTECTED">
            <SecurityPosture />
          </Section>
          <FederationVault />
        </>
      )}

      {active === "learn" && (
        <Section title="LEARN">
          <div className="flex items-center justify-between flex-wrap gap-2">
            <span style={{ fontFamily: MONO, fontSize: 11.5, color: C.mut }}>
              short notes on each screen that say what it is for — off by default
            </span>
            <div className="flex items-center gap-2">
              <button
                onClick={() => startTour()}
                title="a walkthrough of the whole product, stop by stop"
                style={btn({ color: C.steel, borderColor: C.steel, fontSize: 11, padding: "4px 12px" })}
              >
                start tour
              </button>
              <button
                onClick={() => setLearnMode(!learnMode)}
                style={btn({ color: learnMode ? C.steel : C.mut, borderColor: learnMode ? C.steel : C.line, fontSize: 11, padding: "4px 12px" })}
              >
                {learnMode ? "on" : "off"}
              </button>
              <button
                onClick={resetCoach}
                disabled={coachDismissed.length === 0}
                title="bring back every note you closed"
                style={btn({ color: coachDismissed.length ? C.mut : C.dim, fontSize: 11, padding: "4px 12px" })}
              >
                reset tips{coachDismissed.length ? ` (${coachDismissed.length})` : ""}
              </button>
            </div>
          </div>
        </Section>
      )}

      {active === "operator" && <FunnelPanel />}
      {active === "audit" && <AuditTrail />}
    </div>
  );
}

type SettingsTab = "workspace" | "billing" | "connect" | "alerts" | "security" | "audit" | "learn" | "operator";

const PLANE_URL = typeof window === "undefined" ? "" : window.location.origin;

/** Signing in with an access token instead — an operator's, or an API key. */
function TokenFold() {
  const apiToken = useApp((s) => s.apiToken);
  const setApiToken = useApp((s) => s.setApiToken);
  const identityEmail = useApp((s) => s.identityEmail);
  const authStatus = useQuery({ queryKey: ["auth-status", apiToken], queryFn: api.authStatus });
  // signed in with an account: its token is not a thing to show or paste
  if (identityEmail) return null;
  return (
    <details className="mt-3" open={!!apiToken || undefined}>
      <summary style={{ cursor: "pointer", fontFamily: MONO, fontSize: 11, color: C.dim }}>
        use an access token instead {authStatus.data?.authenticated && apiToken ? "· signed in with one" : ""}
      </summary>
      <div className="mt-2">
        <div style={{ fontFamily: MONO, fontSize: 10.5, color: C.dim, marginBottom: 8 }}>
          the operator token of a plane you run yourself, or an API key — sent with every request from this browser
        </div>
        <div className="flex items-center gap-2 mb-2">
          <input type="password" value={apiToken} onChange={(e) => setApiToken(e.target.value)}
            placeholder="access token" aria-label="access token" style={INPUT_STYLE} />
          <span style={{ fontFamily: MONO, fontSize: 11, whiteSpace: "nowrap",
            color: authStatus.data?.authenticated ? C.green : C.red }}>
            {authStatus.data?.authenticated ? "● signed in" : "○ not signed in"}
          </span>
        </div>
        {authStatus.data?.authenticated && (
          <div style={{ fontFamily: MONO, fontSize: 10.5, color: C.dim }}>
            can: {(authStatus.data.scopes ?? []).join(", ") || "nothing"}
          </div>
        )}
      </div>
    </details>
  );
}

const TIER_NAME: Record<string, string> = {
  community: "Free", coding: "Coding", business: "Business", team: "Team", security: "Security Workspace",
  enterprise: "Enterprise",
};

/** One month's money, line by line: the plan, developers past what it
 * includes, governed nodes past what it includes. */
function StatementLines({ st, coding, prod }: { st: BillingStatement; coding: boolean; prod: boolean }) {
  const row = (what: React.ReactNode, amount: string, testid?: string) => (
    <div data-testid={testid} className="flex gap-3" style={{ fontFamily: MONO, fontSize: 11.5, padding: "3px 0", borderTop: `1px solid ${C.line}` }}>
      <span style={{ flex: 1, color: C.mut }}>{what}</span>
      <span style={{ color: C.text }}>{amount}</span>
    </div>
  );
  const devs = st.active_developers;
  return (
    <div>
      {row(<>{TIER_NAME[st.workspace_tier] ?? st.workspace_tier} plan</>, st.priced ? st.base : "contracted")}
      {(coding || devs > 0) && row(
        <>developers whose agents ran: <span style={{ color: C.text }}>{devs}</span>
          {st.included_developers ? ` · ${st.included_developers} included` : ""}
          {st.billable_developers ? ` · ${st.billable_developers} × $25` : ""}</>,
        st.priced ? st.seats : "—", "bill-developers")}
      {(prod || st.peak_nodes > 0) && row(
        <>governed nodes at the peak: <span style={{ color: C.text }}>{st.peak_nodes}</span>
          {st.peak_day ? ` on ${st.peak_day}` : ""} · {st.included_nodes} included
          {st.billable_nodes ? ` · ${st.billable_nodes} over` : ""}</>,
        st.workspace_tier === "coding" && st.billable_nodes > 0 ? "on Team and up"
          : st.priced ? st.overage : "—", "bill-nodes")}
      <div className="flex gap-3" style={{ fontFamily: MONO, fontSize: 12.5, padding: "6px 0 0", borderTop: `1px solid ${C.line}` }}>
        <span style={{ flex: 1, color: C.text }}>{st.provisional ? "so far this month" : "total"}</span>
        <span data-testid="bill-total" style={{ color: C.text, fontWeight: 700 }}>{st.priced ? st.total : "per contract"}</span>
      </div>
      {st.provisional && (
        <div style={{ fontFamily: MONO, fontSize: 10, color: C.dim, marginTop: 3 }}>
          the month is still running — a new developer or a higher peak can still add to it
        </div>
      )}
    </div>
  );
}

/** Billing for both journeys: the plan, this month so far, the closed months —
 * on the free plan, what this month would cost on each paid plan. */
function Billing({ coding, prod }: { coding: boolean; prod: boolean }) {
  const q = useQuery({ queryKey: ["billing"], queryFn: api.billing, retry: false });
  const [open, setOpen] = useState<string | null>(null);
  if (q.isError) {
    return (
      <Section title="PLAN">
        <div style={{ fontFamily: MONO, fontSize: 11.5, color: C.mut }}>
          Billing is for a workspace with an account — sign in, or create one, to see it.
        </div>
      </Section>
    );
  }
  if (!q.data) return null;
  const { plan, current, history, if_paid } = q.data;
  const rungs = if_paid.filter((e) =>
    e.tier === "coding" ? coding : e.tier === "security" ? prod : true);
  return (
    <>
      <Section title="PLAN">
        <div data-testid="billing-plan" style={{ fontFamily: MONO, fontSize: 12, color: C.text, lineHeight: 1.7 }}>
          {TIER_NAME[plan.tier] ?? plan.tier}
          <span style={{ color: C.mut }}>
            {plan.tier === "community" ? " — $0: one developer and one agent family, one governed node"
              : plan.tier === "coding" ? ` — ${plan.seat} per developer whose agents ran that month · production agents: 1 included, then $75 each`
                : plan.priced ? ` — ${plan.base} a month · ${plan.included_developers} developers and ${plan.included_nodes} governed nodes included, then ${plan.seat} per developer`
                  : " — contracted"}
          </span>
        </div>
        {plan.licensed && (
          <div style={{ fontFamily: MONO, fontSize: 10.5, color: C.dim }}>
            {plan.organization}{plan.expires_at ? ` · until ${plan.expires_at}` : ""}
          </div>
        )}
        <button onClick={() => navigate("pricing")} className="mt-2"
          style={{ background: "none", border: "none", padding: 0, cursor: "pointer", fontFamily: MONO, fontSize: 11.5, color: C.steel }}>
          {plan.tier === "community" ? "see the paid plans →" : "change plan →"}
        </button>
      </Section>

      {current && (
        <Section title={`THIS MONTH · ${current.month}`}>
          <div data-testid="billing-current"><StatementLines st={current} coding={coding} prod={prod} /></div>
        </Section>
      )}

      {coding && current && q.data.forecast && (plan.seat_cents ?? 0) > 0 && (
        <Section title="DEVELOPER SEATS · this month">
          <Seats report={q.data} />
        </Section>
      )}

      {rungs.length > 0 && (
        <Section title={`ON A PAID PLAN, THIS MONTH WOULD BE · from your own usage`}>
          <div data-testid="billing-if-paid" className="flex flex-col" style={{ gap: 14 }}>
            {rungs.map((e) => (
              <div key={e.tier} data-testid={`if-paid-${e.tier}`}>
                <div style={{ fontFamily: MONO, fontSize: 11.5, color: C.text, marginBottom: 4 }}>{TIER_NAME[e.tier ?? ""] ?? e.tier}</div>
                <StatementLines st={e} coding={coding} prod={prod} />
              </div>
            ))}
            <div style={{ fontFamily: MONO, fontSize: 10, color: C.dim }}>
              an estimate at list prices — nothing is charged on the free plan
            </div>
          </div>
        </Section>
      )}

      {history.length > 0 && (
        <Section title="STATEMENTS · closed months">
          <div data-testid="billing-history">
            {history.map((h) => (
              <div key={h.month} style={{ borderTop: `1px solid ${C.line}` }}>
                <button onClick={() => setOpen(open === h.month ? null : h.month)}
                  className="flex gap-3 w-full"
                  style={{ background: "none", border: "none", cursor: "pointer", padding: "5px 0", fontFamily: MONO, fontSize: 11.5 }}>
                  <span style={{ color: C.text, width: 70, textAlign: "left" }}>{h.month}</span>
                  <span style={{ color: C.mut, flex: 1, textAlign: "left" }}>
                    {coding ? `${h.active_developers} developer${h.active_developers === 1 ? "" : "s"}` : ""}
                    {coding && prod ? " · " : ""}{prod ? `peak ${h.peak_nodes} nodes` : ""}
                  </span>
                  <span style={{ color: C.text }}>{h.priced ? h.total : "per contract"}</span>
                </button>
                {open === h.month && (
                  <div className="pb-2">
                    <StatementLines st={h} coding={coding} prod={prod} />
                    {h.developers.length > 0 && (
                      <div style={{ fontFamily: MONO, fontSize: 10.5, color: C.dim, marginTop: 6 }}>
                        developers (as the plane knows them — an id each, never an address):{" "}
                        {h.developers.map((d) => `${d.developer.slice(0, 12)}… ${d.sessions} session${d.sessions === 1 ? "" : "s"}`).join(" · ")}
                      </div>
                    )}
                  </div>
                )}
              </div>
            ))}
          </div>
        </Section>
      )}
    </>
  );
}

/** Who holds a seat this month, where the bill is heading, and the
 *  workspace's limit on seats (an admin sets it; past it a developer's
 *  sessions are protected, not shown, not billed). */
function Seats({ report }: { report: BillingReport }) {
  const qc = useQueryClient();
  const admin = useCanAdmin();
  const f = report.forecast!;
  const cur = report.current!;
  const seat = report.plan.seat;
  const [edit, setEdit] = useState<string | null>(null);
  const save = useMutation({
    mutationFn: (cap: number | null) => api.setSeatCap(cap),
    onSuccess: () => {
      setEdit(null);
      for (const k of ["billing", "entitlements", "nodes"]) void qc.invalidateQueries({ queryKey: [k] });
    },
  });
  const seated = cur.developers.filter((d) => d.seated !== false);
  const over = cur.developers.filter((d) => d.seated === false);
  const who = (d: string) => (d.length > 28 ? `${d.slice(0, 27)}…` : d);
  return (
    <div data-testid="billing-seats" style={{ fontFamily: MONO, fontSize: 11.5, color: C.mut, lineHeight: 1.8 }}>
      <div style={{ color: C.text }} data-testid="seats-so-far">
        {f.developers_so_far} developer{f.developers_so_far === 1 ? "" : "s"} so far · {cur.total}
        {cur.included_developers > 0 && <span style={{ color: C.dim }}> · {cur.included_developers} included</span>}
      </div>
      <div data-testid="seats-forecast">
        forecast for the month:{" "}
        <span style={{ color: C.text }}>
          {f.projected_developers} developer{f.projected_developers === 1 ? "" : "s"} → {f.projected_total}
        </span>
        {f.likely.length > 0 ? (
          <span style={{ color: C.dim }}>
            {" "}— {f.likely.slice(0, 4).map((l) => who(l.developer)).join(", ")}
            {f.likely.length > 4 ? ` and ${f.likely.length - 4} more` : ""} ran agents in the last 30 days, not yet this month
          </span>
        ) : <span style={{ color: C.dim }}> — nobody else ran agents in the last 30 days</span>}
        {f.seat_cap !== null && f.projected_developers < f.developers_so_far + f.likely.length && (
          <span style={{ color: C.dim }}> · held to the limit</span>
        )}
      </div>
      <div className="flex items-center gap-2 flex-wrap" data-testid="seat-cap">
        <span>seat limit:</span>
        {edit === null ? (
          <>
            <span style={{ color: C.text }}>
              {f.seat_cap === null ? "none — every developer who runs agents takes a seat"
                : `${f.seat_cap} · ${f.seats_left} left`}
            </span>
            {admin && (
              <button onClick={() => setEdit(f.seat_cap === null ? String(Math.max(f.developers_so_far, 1)) : String(f.seat_cap))}
                data-testid="seat-cap-edit"
                style={{ background: "none", border: "none", padding: 0, cursor: "pointer", color: C.steel, fontFamily: MONO, fontSize: 11 }}>
                {f.seat_cap === null ? "set a limit" : "change"}
              </button>
            )}
            {admin && f.seat_cap !== null && (
              <button onClick={() => save.mutate(null)} data-testid="seat-cap-remove"
                style={{ background: "none", border: "none", padding: 0, cursor: "pointer", color: C.dim, fontFamily: MONO, fontSize: 11 }}>
                remove
              </button>
            )}
          </>
        ) : (
          <form className="flex items-center gap-2" onSubmit={(e) => {
            e.preventDefault();
            const n = Number(edit);
            if (Number.isInteger(n) && n >= 1) save.mutate(n);
          }}>
            <input type="number" min={Math.max(1, f.developers_so_far)} value={edit} aria-label="seat limit"
              onChange={(e) => setEdit(e.target.value)} data-testid="seat-cap-input"
              style={{ ...INPUT_STYLE, width: 70 }} />
            <span style={{ color: C.dim }}>
              at most {seat} × {Number(edit) || 0} = ${((Number(edit) || 0) * (report.plan.seat_cents ?? 0) / 100).toFixed(2)} a month for seats
            </span>
            <button type="submit" disabled={save.isPending} data-testid="seat-cap-save"
              style={btn({ color: C.text, borderColor: C.steel, fontSize: 11, padding: "3px 10px" })}>
              {save.isPending ? "…" : "save"}
            </button>
            <button type="button" onClick={() => setEdit(null)}
              style={{ background: "none", border: "none", padding: 0, cursor: "pointer", color: C.dim, fontFamily: MONO, fontSize: 11 }}>
              cancel
            </button>
          </form>
        )}
      </div>
      {save.isError && <div style={{ color: C.red, fontSize: 11 }} data-testid="seat-cap-error">{errorWords((save.error as Error).message)}</div>}
      <div style={{ color: C.dim, fontSize: 10.5 }}>
        the first developers to run agents in a month hold the seats; past the limit a developer's
        sessions are still protected by their hooks, but not shown, operated or billed — and they are
        told so in their agent. It cannot go below the seats already used this month.
      </div>
      {seated.length > 0 && (
        <div className="mt-2" data-testid="seats-list">
          {seated.map((d, i) => (
            <div key={d.developer} className="flex gap-3" style={{ fontSize: 11 }}>
              <span style={{ color: C.dim, width: 22, textAlign: "right" }}>{i + 1}.</span>
              <span style={{ color: C.text, flex: 1 }}>{who(d.developer)}</span>
              <span style={{ color: C.dim }}>since {d.first_day?.slice(5) ?? "—"} · {d.sessions} session{d.sessions === 1 ? "" : "s"}</span>
            </div>
          ))}
        </div>
      )}
      {over.length > 0 && (
        <div className="mt-1" data-testid="seats-over" style={{ color: C.amber, fontSize: 11 }}>
          past the limit, not billed: {over.map((d) => who(d.developer)).join(", ")}
        </div>
      )}
    </div>
  );
}

/** Coding agents are paid per developer whose agents ran in a month: how many
 * did, what the plan includes, and what the free plan holds to. */
function DeveloperUsage() {
  const usage = useQuery({ queryKey: ["license-usage"], queryFn: () => api.licenseUsage(6) });
  const months = usage.data?.months ?? [];
  return (
    <div data-testid="developer-usage" style={{ fontFamily: MONO, fontSize: 11.5, color: C.mut, lineHeight: 1.7 }}>
      <div style={{ color: C.dim, marginBottom: 8 }}>
        A developer counts once a month when their agents (Claude Code, Codex) report — however many
        sessions, repositories or trees. The example sessions never count.
      </div>
      <div className="flex gap-4 flex-wrap" style={{ fontSize: 11 }}>
        {months.map((m) => (
          <span key={m.month} style={{ color: C.dim }}>{m.month} · <span style={{ color: m.active_developers ? C.text : C.dim }}>
            {m.active_developers} developer{m.active_developers === 1 ? "" : "s"}</span></span>
        ))}
      </div>
    </div>
  );
}

const PRESETS: { id: string; label: string; words: string; scopes: string[]; node: boolean; for: Journey[] }[] = [
  { id: "coding", label: "for the team's coding agents", scopes: ["ingest"], node: false, for: ["coding", "both"],
    words: "each developer connects their machine with it once" },
  { id: "node", label: "for one production agent", scopes: ["ingest"], node: true, for: ["production", "both"],
    words: "bound to one agent: the plane refuses it for any other, so a compromised agent cannot speak for its neighbours" },
  { id: "ci", label: "for CI", scopes: ["read", "ingest"], node: false, for: ["coding", "production", "both"],
    words: "posts checks (skill evals, context health) and reads results" },
  { id: "custom", label: "custom", scopes: [], node: false, for: ["coding", "production", "both"],
    words: "pick what it may do" },
];

// What a key may do, said as a role. The owner changes it in place: the same
// secret on the same machine, a different authority.
const KEY_ROLES: { id: string; label: string; scopes: string[] }[] = [
  { id: "agent", label: "agent — reports", scopes: ["ingest"] },
  { id: "agent+read", label: "agent + reads the plane", scopes: ["read", "ingest"] },
  { id: "operator", label: "operator — steers agents", scopes: ["read", "ingest", "operate"] },
  { id: "admin", label: "admin — everything", scopes: ["read", "ingest", "operate", "admin"] },
];

function KeyRole({ keyId, scopes }: { keyId: string; scopes: string[] }) {
  const qc = useQueryClient();
  const set = useMutation({
    mutationFn: (to: string[]) => api.setKeyScopes(keyId, to),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["api-keys"] });
      void qc.invalidateQueries({ queryKey: ["api-keys-audit"] });
    },
  });
  const have = [...scopes].sort().join(",");
  const role = KEY_ROLES.find((r) => [...r.scopes].sort().join(",") === have);
  return (
    <select value={role?.id ?? "custom"} aria-label={`role of ${keyId}`} disabled={set.isPending}
      onChange={(e) => { const r = KEY_ROLES.find((x) => x.id === e.target.value); if (r) set.mutate(r.scopes); }}
      style={{ ...INPUT_STYLE, width: 170, padding: "1px 4px", fontSize: 10.5 }}>
      {!role && <option value="custom" disabled>{scopes.join(",")}</option>}
      {KEY_ROLES.map((r) => <option key={r.id} value={r.id}>{r.label}</option>)}
    </select>
  );
}

/** A member's own keys: one per machine, ingest only, theirs to revoke. */
function MyKeys() {
  const qc = useQueryClient();
  const mine = useQuery({ queryKey: ["my-keys"], queryFn: api.myKeys, retry: false });
  const [secret, setSecret] = useState<string | null>(null);
  const mint = useMutation({
    mutationFn: api.createMyKey,
    onSuccess: (r) => { setSecret(r.secret); void qc.invalidateQueries({ queryKey: ["my-keys"] }); },
  });
  const revoke = useMutation({
    mutationFn: api.revokeMyKey,
    onSuccess: () => void qc.invalidateQueries({ queryKey: ["my-keys"] }),
  });
  return (
    <div data-testid="my-keys" style={{ fontFamily: MONO, fontSize: 11.5, color: C.mut }}>
      <div style={{ marginBottom: 8 }}>A key for your machine, with what your role can do: your agents report, and you see and steer them.</div>
      {secret ? (
        <>
          <pre data-testid="my-key-connect" style={{ background: C.bg, border: `1px solid ${C.line}`, borderRadius: 5,
            padding: "8px 10px", margin: "0 0 4px", color: C.text, whiteSpace: "pre-wrap", wordBreak: "break-all" }}>
            axor-wrap connect {PLANE_URL} --key {secret}
          </pre>
          <div style={{ color: C.amber, fontSize: 10.5, marginBottom: 8 }}>shown once — run it on your machine now</div>
        </>
      ) : (
        <button data-testid="my-key-create" onClick={() => mint.mutate()} disabled={mint.isPending}
          style={btn({ color: C.steel, borderColor: C.steel, fontSize: 10.5, padding: "4px 10px", marginBottom: 8 })}>
          {mint.isPending ? "…" : "create a key for this machine"}
        </button>
      )}
      {mint.isError && <div style={{ color: C.red }}>{(mint.error as Error).message}</div>}
      {(mine.data ?? []).map((k) => (
        <div key={k.key_id} className="flex gap-3 items-center" style={{ fontSize: 11, marginTop: 4 }}>
          <span style={{ color: C.text }}>{k.key_id}</span>
          <span style={{ color: C.dim }}>{k.created_ts.slice(0, 10)}</span>
          <button onClick={() => revoke.mutate(k.key_id)} style={{ background: "none", border: "none", padding: 0,
            cursor: "pointer", fontFamily: MONO, fontSize: 11, color: C.steel }}>revoke</button>
        </div>
      ))}
    </div>
  );
}

/** API keys, minted for what they are for, with the lines to put them to use. */
function AgentKeys({ journey, menuAll }: { journey: Journey | null; menuAll: boolean }) {
  const qc = useQueryClient();
  const apiToken = useApp((s) => s.apiToken);
  const authStatus = useQuery({ queryKey: ["auth-status", apiToken], queryFn: api.authStatus });
  const admin = (authStatus.data?.scopes ?? []).includes("admin");
  const on = authStatus.data?.auth_enabled === true && authStatus.data?.authenticated === true && admin;
  const presets = PRESETS.filter((p) => !journey || menuAll || p.for.includes(journey));
  const [preset, setPreset] = useState(presets[0]?.id ?? "custom");
  const p = PRESETS.find((x) => x.id === preset) ?? PRESETS[PRESETS.length - 1];
  const [scopes, setScopes] = useState<string[]>(p.scopes);
  const [label, setLabel] = useState("");
  const [node, setNode] = useState("");
  const [minted, setMinted] = useState<{ secret: string; preset: string } | null>(null);
  const keys = useQuery({ queryKey: ["api-keys"], queryFn: api.listKeys, enabled: on, retry: false });
  const keysAudit = useQuery({ queryKey: ["api-keys-audit"], queryFn: () => api.keysAudit(), enabled: on, retry: false });
  const mint = useMutation({
    mutationFn: () => api.createKey(preset === "custom" ? scopes : p.scopes, label || p.label, p.node ? node.trim() : undefined),
    onSuccess: (r) => {
      setMinted({ secret: r.secret, preset });
      void qc.invalidateQueries({ queryKey: ["api-keys"] });
      void qc.invalidateQueries({ queryKey: ["api-keys-audit"] });
    },
  });
  const revoke = useMutation({
    mutationFn: (keyId: string) => api.revokeKey(keyId),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["api-keys"] });
      void qc.invalidateQueries({ queryKey: ["api-keys-audit"] });
    },
  });
  if (authStatus.data && !authStatus.data.auth_enabled) {
    return (
      <div data-testid="keys-open" style={{ fontFamily: MONO, fontSize: 11.5, color: C.mut, lineHeight: 1.7 }}>
        This plane is open — no key needed. A developer's machine:
        <pre style={{ background: C.bg, border: `1px solid ${C.line}`, borderRadius: 5, padding: "8px 10px", margin: "8px 0 0", color: C.text }}>
          axor-wrap connect {PLANE_URL}
        </pre>
        <div style={{ marginTop: 8 }}>A production agent's process: <span style={{ color: C.text }}>AXOR_PLANE_URL={PLANE_URL}</span></div>
      </div>
    );
  }
  if (!on) {
    return authStatus.data?.authenticated ? <MyKeys /> : (
      <div style={{ fontFamily: MONO, fontSize: 11.5, color: C.mut }}>
        Sign in to make a key for your machine.
      </div>
    );
  }
  const env = minted
    ? minted.preset === "node"
      ? `export AXOR_PLANE_URL=${PLANE_URL}\nexport AXOR_NODE_ID=${node.trim()}\nexport AXOR_INGEST_KEY=${minted.secret}`
      : minted.preset === "ci"
        ? `AXOR_PLANE_URL=${PLANE_URL}\nAXOR_INGEST_KEY=${minted.secret}   # as CI secrets`
        : `axor-wrap connect ${PLANE_URL} --key ${minted.secret}`
    : "";
  return (
    <div data-testid="agent-keys">
      <div className="flex gap-2 flex-wrap mb-2">
        {presets.map((x) => (
          <button key={x.id} onClick={() => { setPreset(x.id); setScopes(x.scopes); setMinted(null); }}
            data-testid={`key-preset-${x.id}`}
            style={btn({ color: preset === x.id ? C.steel : C.dim, borderColor: preset === x.id ? C.steel : C.line, fontSize: 10.5, padding: "4px 10px" })}>
            {x.label}
          </button>
        ))}
      </div>
      <div style={{ fontFamily: MONO, fontSize: 10.5, color: C.dim, marginBottom: 8 }}>{p.words}</div>
      <div className="flex items-center gap-2 flex-wrap mb-2">
        {preset === "custom" && KEY_SCOPES.map((sc) => (
          <button key={sc} onClick={() => setScopes((cur) => cur.includes(sc) ? cur.filter((x) => x !== sc) : [...cur, sc])}
            style={btn({ color: scopes.includes(sc) ? C.steel : C.dim, borderColor: scopes.includes(sc) ? C.steel : C.line, fontSize: 10.5, padding: "4px 10px" })}>
            {sc}
          </button>
        ))}
        {p.node && (
          <input value={node} onChange={(e) => setNode(e.target.value)} placeholder="the agent's node id" aria-label="node id"
            style={{ ...INPUT_STYLE, width: 200, fontSize: 11, padding: "4px 8px" }} />
        )}
        <input value={label} onChange={(e) => setLabel(e.target.value)} placeholder="label" aria-label="key label"
          style={{ ...INPUT_STYLE, width: 140, fontSize: 11, padding: "4px 8px" }} />
        <button onClick={() => mint.mutate()} data-testid="key-mint"
          disabled={(preset === "custom" && scopes.length === 0) || (p.node && !node.trim()) || mint.isPending}
          style={btn({ color: C.steel, fontSize: 11 })}>
          {mint.isPending ? <Loader2 size={12} className="animate-spin" /> : null} make a key
        </button>
      </div>
      {minted && (
        <div data-testid="key-minted" className="mb-3 p-2" style={{ background: C.panel2, border: `1px solid ${C.line}`, borderRadius: 5 }}>
          <div className="flex items-center gap-2" style={{ fontFamily: MONO, fontSize: 10, color: C.amber, marginBottom: 4 }}>
            shown once — copy it now
            <Copy size={12} color={C.mut} style={{ cursor: "pointer" }} onClick={() => void navigator.clipboard?.writeText(env)} />
          </div>
          <pre style={{ margin: 0, fontFamily: MONO, fontSize: 10.5, color: C.green, whiteSpace: "pre-wrap", wordBreak: "break-all" }}>{env}</pre>
        </div>
      )}
      {(keys.data ?? []).map((k) => (
        <div key={k.key_id} className="flex items-center gap-2 py-1">
          <span style={{ fontFamily: MONO, fontSize: 11, color: C.text }}>{k.key_id}</span>
          <KeyRole keyId={k.key_id} scopes={k.scopes} />
          <span style={{ fontFamily: MONO, fontSize: 10.5, color: C.dim, flex: 1 }}>
            {k.label}{k.node_id ? ` · only for ${k.node_id}` : ""}
          </span>
          <button aria-label={`revoke ${k.key_id}`} disabled={revoke.isPending}
            onClick={() => {
              if (window.confirm(`Revoke ${k.key_id}? Anything using it loses access.`)) revoke.mutate(k.key_id);
            }}
            style={{ background: "none", border: "none", padding: 0, cursor: "pointer" }}>
            <Trash2 size={12} color={C.dim} />
          </button>
        </div>
      ))}
      {(mint.isError || revoke.isError) && (
        <div className="mb-2" style={{ fontFamily: MONO, fontSize: 11, color: C.red }}>
          {mint.isError ? `could not make the key: ${(mint.error as Error).message}`
            : `could not revoke it: ${(revoke.error as Error).message}`}
        </div>
      )}
      {(keysAudit.data ?? []).length > 0 && (
        <details className="mt-3" data-testid="key-lifecycle-audit">
          <summary style={{ cursor: "pointer", fontFamily: MONO, fontSize: 10.5, color: C.dim, letterSpacing: "0.08em" }}>
            MADE &amp; REVOKED · newest first
          </summary>
          {(keysAudit.data ?? []).map((a) => (
            <div key={a.audit_id} className="flex items-center gap-2 py-0.5">
              <span style={{ fontFamily: MONO, fontSize: 10.5, width: 46, color: a.action === "revoke" ? C.red : C.green }}>{a.action}</span>
              <span style={{ fontFamily: MONO, fontSize: 10.5, color: C.text }}>{a.key_id}</span>
              <span style={{ fontFamily: MONO, fontSize: 10.5, color: C.mut }}>
                {(a.scopes ?? []).join(",")}{a.node_id ? ` @${a.node_id}` : ""}
              </span>
              <span style={{ fontFamily: MONO, fontSize: 10.5, color: C.dim, flex: 1, textAlign: "right" }}>
                by {a.by.id ?? a.by.kind} · {a.ts.slice(0, 19).replace("T", " ")}
              </span>
            </div>
          ))}
        </details>
      )}
    </div>
  );
}

const ago = (ts?: string | null) => {
  if (!ts) return "never";
  const s = Math.max(0, (Date.now() - new Date(ts).getTime()) / 1000);
  return s < 90 ? "just now" : s < 5400 ? `${Math.round(s / 60)} min ago` : s < 172800 ? `${Math.round(s / 3600)} h ago` : `${Math.round(s / 86400)} days ago`;
};

/** What reports to this plane: production agents one by one — when last
 * heard from, at what level, whether they hold a key of their own — and the
 * coding agents per developer. The example's are left out. */
function Connections({ coding, prod }: { coding: boolean; prod: boolean }) {
  const nodes = useQuery({ queryKey: ["nodes"], queryFn: api.nodes, refetchInterval: 15000 });
  const apiToken = useApp((s) => s.apiToken);
  const authStatus = useQuery({ queryKey: ["auth-status", apiToken], queryFn: api.authStatus });
  const admin = authStatus.data?.auth_enabled === true && (authStatus.data?.scopes ?? []).includes("admin");
  const keys = useQuery({ queryKey: ["api-keys"], queryFn: api.listKeys, enabled: admin, retry: false });
  const bound = new Set((keys.data ?? []).map((k) => k.node_id).filter(Boolean));
  const all = (nodes.data ?? []).filter((n) => !/example/.test(n.node_id) && !n.profile?.developer?.startsWith("dev-example-"));
  const agents = all.filter((n) => !n.profile || n.profile.kind === "agent");
  // coding agents per developer this month, from the seat meter (activity, not
  // heartbeats — a session that never heartbeat is still a developer's)
  const billing = useQuery({ queryKey: ["billing"], queryFn: api.billing, retry: false });
  const month = billing.data?.current?.developers ?? billing.data?.if_paid?.[0]?.developers ?? [];
  const last = new Map<string, string>();
  for (const n of all.filter((x) => x.profile?.kind === "coding")) {
    const t = n.reported?.updated_ts ?? "";
    if (t > (last.get(n.profile!.developer) ?? "")) last.set(n.profile!.developer, t);
  }
  const devs = new Map(month.map((d) => [d.developer, {
    sessions: d.sessions, agents: new Set(d.agents), last: last.get(d.developer) ?? "" }]));
  const sessionCount = month.reduce((n, d) => n + d.sessions, 0);
  const row: React.CSSProperties = { fontFamily: MONO, fontSize: 11, padding: "3px 0", borderTop: `1px solid ${C.line}` };
  return (
    <div data-testid="connections">
      {prod && (
        <div className="mb-3">
          <div style={{ fontFamily: MONO, fontSize: 10.5, color: C.dim, marginBottom: 4 }}>
            PRODUCTION AGENTS · {agents.length}
          </div>
          {agents.length === 0 && <div style={{ fontFamily: MONO, fontSize: 11, color: C.dim }}>none has reported yet</div>}
          {agents.slice(0, 50).map((n) => {
            const stale = !n.reported || Date.now() - new Date(n.reported.updated_ts).getTime() > 120_000;
            return (
              <div key={n.node_id} data-testid={`conn-${n.node_id}`} className="flex gap-3" style={row}>
                <span style={{ color: C.text, flex: 1, overflow: "hidden", textOverflow: "ellipsis" }}>{n.node_id}</span>
                <span style={{ color: n.reported?.level && n.reported.level !== "NORMAL" ? C.amber : C.mut }}>{n.reported?.level?.toLowerCase() ?? "—"}</span>
                <span style={{ color: stale ? C.amber : C.green, width: 90, textAlign: "right" }}>{ago(n.reported?.updated_ts)}</span>
                {admin && <span style={{ color: bound.has(n.node_id) ? C.green : C.dim, width: 110, textAlign: "right" }}>
                  {bound.has(n.node_id) ? "its own key" : "a shared key"}</span>}
              </div>
            );
          })}
          {admin && agents.some((n) => !bound.has(n.node_id)) && (
            <div style={{ fontFamily: MONO, fontSize: 10.5, color: C.dim, marginTop: 4 }}>
              an agent on a shared key can speak for its neighbours — give each one its own (above, "for one production agent")
            </div>
          )}
        </div>
      )}
      {coding && (
        <div>
          <div style={{ fontFamily: MONO, fontSize: 10.5, color: C.dim, marginBottom: 4 }}>
            CODING AGENTS THIS MONTH · {devs.size} developer{devs.size === 1 ? "" : "s"}, {sessionCount} session{sessionCount === 1 ? "" : "s"}
          </div>
          {devs.size === 0 && <div style={{ fontFamily: MONO, fontSize: 11, color: C.dim }}>no developer's agents have reported yet</div>}
          {[...devs.entries()].map(([dev, d]) => (
            <div key={dev} className="flex gap-3" style={row}>
              <span style={{ color: C.text, flex: 1 }}>{dev.slice(0, 16)}…</span>
              <span style={{ color: C.mut }}>{[...d.agents].join(", ")}</span>
              <span style={{ color: C.mut, width: 90, textAlign: "right" }}>{d.sessions} session{d.sessions === 1 ? "" : "s"}</span>
              <span style={{ color: C.dim, width: 90, textAlign: "right" }}>{d.last ? ago(d.last) : ""}</span>
            </div>
          ))}
          <div style={{ fontFamily: MONO, fontSize: 10.5, color: C.dim, marginTop: 4 }}>
            a developer shows as an id — the plane is never told an address
          </div>
        </div>
      )}
    </div>
  );
}

/** Each wall of this plane, up or down, with how to raise the ones that are down. */
function SecurityPosture() {
  const posture = useQuery({ queryKey: ["security-posture"], queryFn: api.securityPosture, retry: false });
  const sealing = useQuery({ queryKey: ["vault-sealing", ""], queryFn: api.vaultSealingKey, retry: false });
  const items = [
    ...(posture.data?.items ?? []),
    ...(sealing.data ? [{ id: "vault_custody", ok: sealing.data.envelope_mode,
      what: "tool credentials are stored sealed — the plane holds only what it cannot open",
      fix: "make a sealing key with axor-proxy vault keygen and register its public half below" }] : []),
  ];
  if (!items.length) return <div style={{ fontFamily: MONO, fontSize: 11, color: C.dim }}>…</div>;
  const down = items.filter((i) => !i.ok).length;
  return (
    <div data-testid="security-posture">
      <div style={{ fontFamily: MONO, fontSize: 11.5, color: down ? C.amber : C.green, marginBottom: 8 }}>
        {down ? `${down} of ${items.length} down — fine on your own machine, not for a deployment` : "every wall is up"}
      </div>
      {items.map((i) => (
        <div key={i.id} data-testid={`posture-${i.id}`} className="flex gap-2" style={{ fontFamily: MONO, fontSize: 11, lineHeight: 1.6, padding: "2px 0" }}>
          <span style={{ color: i.ok ? C.green : C.amber, width: 14 }}>{i.ok ? "✓" : "○"}</span>
          <span style={{ color: i.ok ? C.mut : C.text, flex: 1 }}>
            {i.what}
            {!i.ok && <span style={{ color: C.dim }}> — {i.fix}</span>}
          </span>
        </div>
      ))}
    </div>
  );
}

// Federation vault (spec v2 Ch.5): TWO panes, visibly separate — "manage our
// API keys" must never be conflated with "manage who can command the
// federation". The separation is backend-enforced (separate credentials);
// the UI mirrors it structurally.
function FederationVault() {
  // Both reads are gated by the signing-token, so it is part of the key:
  // typing the token refetches instead of leaving a stale 403 on screen.
  const vaultSigningToken = useApp((s) => s.vaultSigningToken);
  const keys = useQuery({
    queryKey: ["vault-keys", vaultSigningToken], queryFn: api.vaultSigningKeys,
  });
  const audit = useQuery({
    queryKey: ["vault-audit", vaultSigningToken], queryFn: () => api.vaultSigningAudit(),
  });
  const qc = useQueryClient();

  // Signed command posture (protocol §6). The token authorizes sign requests;
  // the key_id selects which custody key signs. Both persist in the store so the
  // command path (api.command / api.appendFact) can sign without prompting.
  const signingKeyId = useApp((s) => s.signingKeyId);
  const setSigningKeyId = useApp((s) => s.setSigningKeyId);
  const setVaultSigningToken = useApp((s) => s.setVaultSigningToken);
  const [newKeyId, setNewKeyId] = useState("");
  const [newKeyOps, setNewKeyOps] = useState("op_ui");

  const createKey = useMutation({
    mutationFn: () =>
      api.createSigningKey(
        newKeyId.trim(),
        newKeyOps.split(",").map((s) => s.trim()).filter(Boolean),
      ),
    onSuccess: (r) => {
      setSigningKeyId(r.key_id);
      setNewKeyId("");
      void qc.invalidateQueries({ queryKey: ["vault-keys"] });
    },
  });

  const signedPosture = Boolean(signingKeyId);
  return (
    <div className="flex gap-4 mb-4 stack-narrow" data-testid="federation-vault" style={{ alignItems: "stretch" }}>
      <ToolCredentials />
      <div className="p-4 flex-1" style={{ background: C.panel, border: `1px solid ${C.line}`, borderRadius: 8 }}>
        <div style={{ fontSize: 10, fontFamily: MONO, color: C.dim, letterSpacing: "0.1em", marginBottom: 8 }}>
          SIGNING COMMANDS TO AGENTS
        </div>
        <div style={{ fontFamily: MONO, fontSize: 10.5, color: C.mut, marginBottom: 10, lineHeight: 1.6 }}>
          Pause, stop and cap reach an agent signed, so a forged command is refused. The
          private key stays in this plane's vault and is never handed out; every signature
          is logged with who asked. Agents check against the public key in their own config —
          they never fetch one from here.
        </div>
        {keys.isError ? (
          <div style={{ fontFamily: MONO, fontSize: 11, color: C.red }}>
            cannot list signing keys: {(keys.error as Error).message}
            {!vaultSigningToken && " — set the vault signing-token below"}
          </div>
        ) : (keys.data ?? []).length === 0 ? (
          <div style={{ fontFamily: MONO, fontSize: 11, color: C.dim }}>no keys under custody</div>
        ) : (
          (keys.data ?? []).map((k) => (
            <div key={k.key_id} className="py-1" style={{ fontFamily: MONO, fontSize: 11 }}>
              <span style={{ color: C.text }}>{k.key_id}</span>
              <span style={{ color: C.dim, fontSize: 10 }}> · {k.public_key_hex.slice(0, 16)}… · signers: {k.operators.join(", ")}</span>
            </div>
          ))
        )}
        {(audit.data ?? []).length > 0 && (
          <div className="mt-2 pt-2" data-testid="sign-request-audit"
            style={{ borderTop: `1px solid ${C.line}` }}>
            <div style={{ fontFamily: MONO, fontSize: 9.5, color: C.dim, letterSpacing: "0.08em", marginBottom: 4 }}>SIGN-REQUEST AUDIT</div>
            {/* The rows arrive NEWEST FIRST. This was `slice(-5).reverse()`,
                which was right while the route returned oldest-first and became
                "the five OLDEST of the page, upside down" when it stopped —
                a log panel showing stale rows as the latest. */}
            {(audit.data ?? []).map((a, i) => (
              <div key={i} className="flex items-center gap-2 py-0.5" style={{ fontFamily: MONO, fontSize: 10 }}>
                <span style={{ color: a.granted ? C.green : C.red }}>{a.granted ? "signed" : "refused"}</span>
                <span style={{ color: C.text }}>{a.operator}</span>
                <span style={{ color: C.dim }}>{a.key_id} · {a.payload_sha256.slice(0, 12)}…</span>
              </div>
            ))}
          </div>
        )}

        {/* Operator command posture: arm signing without the key ever touching
            the browser — the vault signs, the browser holds only the token. */}
        <div className="mt-3 pt-3" style={{ borderTop: `1px solid ${C.line}` }}>
          <div style={{ fontFamily: MONO, fontSize: 9.5, color: C.dim, letterSpacing: "0.08em", marginBottom: 6 }}>
            SIGN THIS BROWSER'S COMMANDS
          </div>
          <div className="flex flex-col gap-2" data-testid="signing-controls">
            <input
              type="password"
              value={vaultSigningToken}
              onChange={(e) => setVaultSigningToken(e.target.value)}
              placeholder="vault signing-token (X-Vault-Signing-Token)"
              aria-label="vault signing-token"
              style={{ background: C.bg, border: `1px solid ${C.line}`, borderRadius: 4, color: C.text, fontFamily: MONO, fontSize: 11, padding: "5px 8px", outline: "none" }}
            />
            <div className="flex items-center gap-2">
              <select
                value={signingKeyId}
                onChange={(e) => setSigningKeyId(e.target.value)}
                aria-label="signing key id"
                style={{ background: C.bg, border: `1px solid ${C.line}`, borderRadius: 4, color: C.text, fontFamily: MONO, fontSize: 11, padding: "5px 8px", outline: "none", flex: 1 }}
              >
                <option value="">— unsigned (a plane on your own machine only) —</option>
                {(keys.data ?? []).map((k) => (
                  <option key={k.key_id} value={k.key_id}>{k.key_id}</option>
                ))}
              </select>
            </div>
            <div className="flex items-center gap-2 flex-wrap">
              <input
                value={newKeyId}
                onChange={(e) => setNewKeyId(e.target.value)}
                placeholder="new key_id"
                aria-label="new signing key id"
                style={{ background: C.bg, border: `1px solid ${C.line}`, borderRadius: 4, color: C.text, fontFamily: MONO, fontSize: 11, padding: "5px 8px", width: 130, outline: "none" }}
              />
              <input
                value={newKeyOps}
                onChange={(e) => setNewKeyOps(e.target.value)}
                placeholder="operators (comma-sep)"
                aria-label="signing key operators"
                style={{ background: C.bg, border: `1px solid ${C.line}`, borderRadius: 4, color: C.text, fontFamily: MONO, fontSize: 11, padding: "5px 8px", flex: 1, outline: "none" }}
              />
              <button
                onClick={() => createKey.mutate()}
                disabled={!newKeyId.trim() || createKey.isPending}
                style={btn({ color: C.steel, fontSize: 10.5, padding: "4px 10px" })}
              >
                {createKey.isPending ? <Loader2 size={11} className="animate-spin" /> : null} create signing key
              </button>
            </div>
            {createKey.isError && (
              <div style={{ fontFamily: MONO, fontSize: 10.5, color: C.red }}>
                {(createKey.error as Error).message}
              </div>
            )}
            <div style={{ fontFamily: MONO, fontSize: 10, color: signedPosture ? C.green : C.amber, lineHeight: 1.6 }}>
              {signedPosture
                ? `● signed — commands from this browser are signed by the vault with ${signingKeyId}; the key itself never comes to the browser.`
                : "○ unsigned — commands from this browser go out without a signature, and only a plane that allows that (AXOR_ALLOW_UNSIGNED=1) takes them. Pick or create a key to sign them."}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="p-4 mb-4" style={{ background: C.panel, border: `1px solid ${C.line}`, borderRadius: 8 }}>
      <div style={{ fontSize: 10, fontFamily: MONO, color: C.dim, letterSpacing: "0.1em", marginBottom: 12 }}>
        {title}
      </div>
      {children}
    </div>
  );
}

// The sign-up funnel (axor_backend/funnel.py) — shown only to the operator:
// the report answers 401/403 to anyone else, and then this renders nothing.
function FunnelPanel() {
  const [days, setDays] = useState(30);
  const report = useQuery({
    queryKey: ["funnel", days], queryFn: () => api.funnelReport(days), retry: false,
  });
  if (!report.data) return null;
  const r = report.data;
  // Steps are counted per tab, and a later step can happen in another tab
  // (sign-up opened from a link), so a ratio can pass 100%: shown as such.
  const pct = (v: number | null) =>
    v == null ? "—" : v > 1 ? "100%+" : `${Math.round(v * 100)}%`;
  return (
    <Section title="SIGN-UP FUNNEL · operator">
      <div className="flex items-center gap-2 mb-2" style={{ fontFamily: MONO, fontSize: 11, color: C.mut }}>
        last
        {[7, 30, 90].map((d) => (
          <button key={d} onClick={() => setDays(d)}
            style={btn({ fontSize: 10.5, padding: "2px 8px", color: d === days ? C.text : C.dim,
                         borderColor: d === days ? C.steel : C.line })}>{d}d</button>
        ))}
        <span style={{ color: C.dim }}>· anonymous tabs, first-party, Do Not Track honoured</span>
      </div>
      <table data-testid="funnel-table" style={{ fontFamily: MONO, fontSize: 11.5, borderCollapse: "collapse", width: "100%" }}>
        <thead>
          <tr style={{ color: C.dim, textAlign: "left" }}>
            <th style={{ padding: "4px 6px" }}>step</th>
            <th style={{ padding: "4px 6px", textAlign: "right" }}>tabs</th>
            <th style={{ padding: "4px 6px", textAlign: "right" }}>of previous</th>
            <th style={{ padding: "4px 6px", textAlign: "right" }}>of visits</th>
          </tr>
        </thead>
        <tbody>
          {r.steps.map((s) => (
            <tr key={s.step} style={{ borderTop: `1px solid ${C.line}` }}>
              <td style={{ padding: "4px 6px", color: C.text }}>{s.step}</td>
              <td style={{ padding: "4px 6px", textAlign: "right", color: C.text }}>{s.count}</td>
              <td style={{ padding: "4px 6px", textAlign: "right", color: C.mut }}>{pct(s.from_previous)}</td>
              <td style={{ padding: "4px 6px", textAlign: "right", color: C.mut }}>{pct(s.from_visit)}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <div style={{ fontFamily: MONO, fontSize: 11, color: C.mut, marginTop: 8, lineHeight: 1.6 }}>
        real agents connected: <span style={{ color: C.text }}>{r.agents_connected}</span>
        {Object.keys(r.signup_prompts).length > 0 && (
          <> · sign-up asked by: {Object.entries(r.signup_prompts)
            .sort((a, b) => b[1] - a[1]).map(([f, n]) => `${f} ${n}`).join(", ")}</>
        )}
      </div>
    </Section>
  );
}
