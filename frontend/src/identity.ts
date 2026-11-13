// Client for the axor-identity login service.
//
// A human logs in here (email + password) and the returned access token becomes
// the bearer the control-plane backend verifies against the identity JWKS. This
// is separate from the operator master token and scoped API keys — those are
// still pasted in Settings and keep working. The base URL is VITE_IDENTITY_URL
// (default `/identity`, so a reverse proxy can host identity on one origin).

export const IDENTITY_BASE = (import.meta.env.VITE_IDENTITY_URL ?? "/identity").replace(/\/$/, "");

export interface Session {
  access_token: string;
  refresh_token: string;
  user: { user_id: string; email: string };
  org: { org_id: string; role: string; tier: string };
}

export class IdentityError extends Error {
  constructor(
    readonly status: number,
    message: string,
  ) {
    super(message);
  }
}

async function post<T>(path: string, body: unknown): Promise<T> {
  let resp: Response;
  try {
    resp = await fetch(`${IDENTITY_BASE}${path}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
  } catch {
    throw new IdentityError(0, "cannot reach the identity service");
  }
  const text = await resp.text();
  let payload: any = {};
  try {
    payload = text ? JSON.parse(text) : {};
  } catch {
    // An HTML page here means nothing is proxying the identity service.
    throw new IdentityError(resp.status, "cannot reach the identity service");
  }
  if (!resp.ok) {
    // FastAPI validation errors carry a list of {loc, msg}; show the messages.
    const detail =
      typeof payload?.detail === "string"
        ? payload.detail
        : Array.isArray(payload?.detail) && payload.detail.length
          ? payload.detail.map((d: { msg?: string }) => d?.msg ?? "invalid input").join("; ")
          : `login failed (${resp.status})`;
    throw new IdentityError(resp.status, detail);
  }
  return payload as T;
}

export function login(email: string, password: string, orgId?: string): Promise<Session> {
  const body: Record<string, string> = { email, password };
  if (orgId) body.org_id = orgId;
  return post<Session>("/v1/login", body);
}

export function signup(email: string, password: string, orgName: string): Promise<Session> {
  // the verification link it mails opens this page
  return post<Session>("/v1/signup", { email, password, org_name: orgName, return_to: here() });
}

/** This page, as a way back for an e-mailed link or a provider sign-in. */
export function here(): string {
  const params = new URLSearchParams(window.location.search);
  for (const k of ["axor_login", "axor_login_error", "axor_verify", "axor_reset"]) params.delete(k);
  const query = params.toString();
  return window.location.pathname + (query ? `?${query}` : "") + window.location.hash;
}

/** Whether this install sends mail (reset links, verification). */
export async function emailAvailable(): Promise<boolean> {
  try {
    const resp = await fetch(`${IDENTITY_BASE}/v1/email`);
    if (!resp.ok) return false;
    return Boolean(((await resp.json()) as { available?: boolean }).available);
  } catch {
    return false;
  }
}

export function forgotPassword(email: string): Promise<{ detail: string }> {
  return post<{ detail: string }>("/v1/password/forgot", { email, return_to: here() });
}

export function resetPassword(token: string, password: string): Promise<unknown> {
  return post<unknown>("/v1/password/reset", { token, password });
}

export function verifyEmail(token: string): Promise<{ email: string }> {
  return post<{ email: string }>("/v1/email/verify", { token });
}

/** Exchange a refresh token for a new access token, rotating it. Returns the
 * new session, or null if the refresh token is no longer valid. */
export async function refresh(refreshToken: string): Promise<Session | null> {
  try {
    return await post<Session>("/v1/refresh", { refresh_token: refreshToken });
  } catch {
    return null;
  }
}

/** Revoke the refresh token server-side. Best effort: the local session is
 * cleared either way, but without this the token stays usable until expiry. */
export async function logout(refreshToken: string): Promise<void> {
  if (!refreshToken) return;
  try {
    await post<unknown>("/v1/logout", { refresh_token: refreshToken });
  } catch {
    /* already revoked or unreachable — nothing more to do */
  }
}

/** Whether this install still takes new accounts. A self-hosted install in
 * `first-only` mode stops after its owner, so offering "create a workspace"
 * there is offering a form that can only 403. Null when unknown (an older
 * identity service without the route): the form then stays, as before. */
export async function signupAvailable(): Promise<boolean | null> {
  try {
    const resp = await fetch(`${IDENTITY_BASE}/v1/signup-mode`);
    if (!resp.ok) return null;
    const body = (await resp.json()) as { available?: boolean };
    return typeof body.available === "boolean" ? body.available : null;
  } catch {
    return null;
  }
}

// ── sign in with GitHub / Google ─────────────────────────────────────────────
export interface OAuthProvider {
  name: string;
  label: string;
}

/** The providers the identity service has set up; empty when none (or an
 * older service without the route). */
export async function oauthProviders(): Promise<OAuthProvider[]> {
  try {
    const resp = await fetch(`${IDENTITY_BASE}/v1/oauth/providers`);
    if (!resp.ok) return [];
    const body = (await resp.json()) as { providers?: OAuthProvider[] };
    return Array.isArray(body.providers) ? body.providers : [];
  } catch {
    return [];
  }
}

/** Where to send the browser to sign in with `provider`. It comes back to this
 * page with `?axor_login=<code>` (or `?axor_login_error=`). */
export function oauthStartUrl(provider: string): string {
  const back = here();
  return `${IDENTITY_BASE}/v1/oauth/${encodeURIComponent(provider)}/start?return_to=${encodeURIComponent(back)}`;
}

/** Trade the one-time code from the redirect for a session. `created` is true
 * when the sign-in made a new account. */
export function oauthExchange(code: string): Promise<Session & { created: boolean }> {
  return post<Session & { created: boolean }>("/v1/oauth/exchange", { code });
}

/** What a provider sign-in or an e-mailed link left in the address bar,
 * removed from it so a reload or a copied link does not carry it. */
export function takeAuthReturn(): { code?: string; error?: string; verify?: string; reset?: string } | null {
  const params = new URLSearchParams(window.location.search);
  const got = {
    code: params.get("axor_login") ?? undefined,
    error: params.get("axor_login_error") ?? undefined,
    verify: params.get("axor_verify") ?? undefined,
    reset: params.get("axor_reset") ?? undefined,
  };
  if (!got.code && !got.error && !got.verify && !got.reset) return null;
  window.history.replaceState(null, "", here());
  return got;
}
