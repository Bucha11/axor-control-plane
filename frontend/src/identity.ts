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
  return post<Session>("/v1/signup", { email, password, org_name: orgName });
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
