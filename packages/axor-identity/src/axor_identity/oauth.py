"""Sign in with GitHub / Google: the provider half of the OAuth flow.

Authorization-code flow with PKCE. A provider is on when both its client id and
secret are set (``AXOR_IDENTITY_GITHUB_CLIENT_ID`` / ``..._SECRET``, likewise
``GOOGLE``). This module only talks to the provider: building the authorize URL,
trading the code for a token, and reading back a *verified* e-mail plus the
provider's stable subject id. Accounts, linking and sessions live in app.py.

The HTTP client is injected so tests can stand a fake provider up with
``httpx.MockTransport`` — nothing here reaches the network on import.
"""
from __future__ import annotations

import base64
import hashlib
import os
import secrets
from dataclasses import dataclass
from urllib.parse import urlencode

import httpx


class OAuthError(Exception):
    """The provider refused, or did not vouch for an e-mail address."""


@dataclass(frozen=True)
class Profile:
    subject: str        # the provider's stable user id — what an identity links by
    email: str          # verified by the provider, lower-cased
    display: str        # a name to title a brand-new workspace with


@dataclass(frozen=True)
class Provider:
    name: str
    label: str
    client_id: str
    client_secret: str
    authorize_url: str
    token_url: str
    scope: str

    def authorize(self, *, redirect_uri: str, state: str, challenge: str) -> str:
        params = {
            "client_id": self.client_id, "redirect_uri": redirect_uri,
            "scope": self.scope, "state": state, "response_type": "code",
            "code_challenge": challenge, "code_challenge_method": "S256",
        }
        if self.name == "google":
            params["prompt"] = "select_account"
        else:
            params["allow_signup"] = "true"
        return f"{self.authorize_url}?{urlencode(params)}"

    async def exchange(self, http: httpx.AsyncClient, *, code: str,
                       redirect_uri: str, verifier: str) -> str:
        resp = await http.post(self.token_url, headers={"Accept": "application/json"}, data={
            "client_id": self.client_id, "client_secret": self.client_secret,
            "code": code, "redirect_uri": redirect_uri, "code_verifier": verifier,
            "grant_type": "authorization_code",
        })
        body = _json(resp)
        token = body.get("access_token")
        if resp.status_code >= 400 or not token:
            raise OAuthError(f"{self.label} refused the sign-in"
                             f" ({body.get('error') or resp.status_code})")
        return str(token)

    async def profile(self, http: httpx.AsyncClient, token: str) -> Profile:
        auth = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
        if self.name == "github":
            return await _github_profile(http, auth)
        return await _google_profile(http, auth)


async def _github_profile(http: httpx.AsyncClient, auth: dict) -> Profile:
    user = _json(await http.get("https://api.github.com/user", headers=auth))
    emails = _json(await http.get("https://api.github.com/user/emails", headers=auth))
    if not user.get("id") or not isinstance(emails, list):
        raise OAuthError("GitHub did not return the account")
    verified = [e for e in emails if isinstance(e, dict) and e.get("verified") and e.get("email")]
    # the primary address if GitHub vouches for it, else any address it vouches for
    verified.sort(key=lambda e: not e.get("primary"))
    if not verified:
        raise OAuthError("your GitHub account has no verified e-mail address")
    return Profile(subject=str(user["id"]), email=str(verified[0]["email"]).strip().lower(),
                   display=str(user.get("login") or user.get("name") or ""))


async def _google_profile(http: httpx.AsyncClient, auth: dict) -> Profile:
    info = _json(await http.get(
        "https://openidconnect.googleapis.com/v1/userinfo", headers=auth))
    if not info.get("sub") or not info.get("email"):
        raise OAuthError("Google did not return the account")
    if info.get("email_verified") not in (True, "true"):
        raise OAuthError("Google has not verified this e-mail address")
    email = str(info["email"]).strip().lower()
    return Profile(subject=str(info["sub"]), email=email,
                   display=str(info.get("given_name") or info.get("name") or ""))


def _json(resp: httpx.Response):  # noqa: ANN202 - dict | list
    try:
        return resp.json()
    except ValueError:
        return {}


_CATALOG = {
    "github": ("GitHub", "https://github.com/login/oauth/authorize",
               "https://github.com/login/oauth/access_token", "read:user user:email"),
    "google": ("Google", "https://accounts.google.com/o/oauth2/v2/auth",
               "https://oauth2.googleapis.com/token", "openid email profile"),
}


def providers_from_env(env: dict[str, str] | None = None) -> dict[str, Provider]:
    """The providers with both a client id and a secret. One without the other
    is a half-done setup, and offering its button would only end in an error
    page at the provider, so it stays off."""
    env = os.environ if env is None else env
    out = {}
    for name, (label, auth_url, token_url, scope) in _CATALOG.items():
        cid = (env.get(f"AXOR_IDENTITY_{name.upper()}_CLIENT_ID") or "").strip()
        secret = (env.get(f"AXOR_IDENTITY_{name.upper()}_CLIENT_SECRET") or "").strip()
        if cid and secret:
            out[name] = Provider(name, label, cid, secret, auth_url, token_url, scope)
    return out


def pkce_pair() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(48)
    digest = hashlib.sha256(verifier.encode()).digest()
    return verifier, base64.urlsafe_b64encode(digest).rstrip(b"=").decode()


def digest(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()
