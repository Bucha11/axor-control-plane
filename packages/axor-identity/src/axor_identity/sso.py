"""Single sign-on for a workspace, over OpenID Connect.

A workspace's admin registers this service as an app in their identity
provider (Okta, Microsoft Entra ID, Google Workspace, Auth0, Keycloak — any
OIDC provider) and gives us its issuer, a client id and secret, and the e-mail
domains it signs people in for. Then:

- the login form takes a work address, finds the workspace by its domain, and
  sends the browser to the provider (authorization-code flow, PKCE, a nonce);
- the provider sends it back with a code; we trade it for an ID token, check
  its signature against the provider's published keys, its issuer, audience,
  expiry and nonce, and read the person's address from it;
- the person signs into the workspace — their account made, or linked, and a
  membership added with the workspace's default role if they had none.

A domain is the workspace's only if an owner or admin of it proved an address
there (their own address, verified) and no other workspace has it; public
mailbox domains are never anyone's. Without that, a workspace could point its
provider at someone else's company and sign in as its people.

This module only talks to the provider. Accounts, sessions and the HTTP routes
live in app.py; the HTTP client is injected, so tests stand a fake provider up
with ``httpx.MockTransport``.
"""
from __future__ import annotations

import base64
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode

import httpx
import jwt

from axor_identity.oauth import digest

PREFIX = "sso:"                  # the provider name an SSO identity is linked under
DEFAULT_ROLES = ("member", "viewer")
ALGORITHMS = ("RS256", "RS384", "RS512", "PS256", "ES256", "ES384", "EdDSA")
DISCOVERY_TTL = 3600

# addresses anyone can have: a domain here is no company's to claim
PUBLIC_DOMAINS = frozenset({
    "gmail.com", "googlemail.com", "outlook.com", "hotmail.com", "live.com", "msn.com",
    "yahoo.com", "ymail.com", "icloud.com", "me.com", "mac.com", "aol.com", "proton.me",
    "protonmail.com", "pm.me", "gmx.com", "gmx.net", "gmx.de", "web.de", "mail.com",
    "yandex.ru", "yandex.com", "ya.ru", "mail.ru", "inbox.ru", "list.ru", "bk.ru",
    "qq.com", "163.com", "126.com", "zoho.com", "fastmail.com", "hey.com", "tutanota.com",
})


class SSOError(Exception):
    """The provider refused, or what it sent back does not hold."""


@dataclass(frozen=True)
class Config:
    org_id: str
    issuer: str
    client_id: str
    client_secret: str
    domains: tuple[str, ...]
    enforce: bool
    default_role: str

    @staticmethod
    def of(row: dict[str, Any]) -> Config:
        return Config(row["org_id"], row["issuer"], row["client_id"], row["client_secret"],
                      tuple(row["domains"]), bool(row["enforce"]), row["default_role"])


def domain_of(email: str) -> str:
    return email.rsplit("@", 1)[-1].strip().lower() if "@" in email else ""


def nonce_for(verifier: str) -> str:
    """The nonce sent with a sign-in: derived from its PKCE verifier, which never
    leaves this service — so it needs no storage of its own."""
    return digest("nonce:" + verifier)[:43]


_discovery: dict[str, tuple[float, dict[str, Any]]] = {}


async def discover(http: httpx.AsyncClient, issuer: str, *, fresh: bool = False) -> dict[str, Any]:
    """The provider's own description of itself (OpenID Connect Discovery)."""
    issuer = issuer.rstrip("/")
    held = _discovery.get(issuer)
    if held and not fresh and time.time() - held[0] < DISCOVERY_TTL:
        return held[1]
    try:
        resp = await http.get(f"{issuer}/.well-known/openid-configuration")
    except httpx.HTTPError as exc:
        raise SSOError(f"the identity provider at {issuer} could not be reached") from exc
    doc = _json(resp)
    if resp.status_code >= 400 or not isinstance(doc, dict):
        raise SSOError(f"{issuer} does not describe itself as an OpenID provider "
                       f"(no /.well-known/openid-configuration)")
    for key in ("authorization_endpoint", "token_endpoint", "jwks_uri"):
        if not isinstance(doc.get(key), str) or not doc[key].startswith("https://") \
                and not doc[key].startswith("http://localhost") \
                and not doc[key].startswith("http://127.0.0.1"):
            raise SSOError(f"the provider's description has no usable {key}")
    if str(doc.get("issuer", "")).rstrip("/") != issuer:
        raise SSOError(f"the provider calls itself {doc.get('issuer')!r}, not {issuer!r}")
    _discovery[issuer] = (time.time(), doc)
    return doc


def authorize_url(doc: dict[str, Any], cfg: Config, *, redirect_uri: str, state: str,
                  challenge: str, nonce: str, login_hint: str = "") -> str:
    params = {"client_id": cfg.client_id, "redirect_uri": redirect_uri, "response_type": "code",
              "scope": "openid email profile", "state": state, "nonce": nonce,
              "code_challenge": challenge, "code_challenge_method": "S256"}
    if login_hint:
        params["login_hint"] = login_hint
    sep = "&" if "?" in doc["authorization_endpoint"] else "?"
    return f"{doc['authorization_endpoint']}{sep}{urlencode(params)}"


async def exchange(http: httpx.AsyncClient, doc: dict[str, Any], cfg: Config, *, code: str,
                   redirect_uri: str, verifier: str) -> str:
    """The code for an ID token. The client authenticates the way the provider
    says it takes (client_secret_basic, the default, or client_secret_post)."""
    data = {"grant_type": "authorization_code", "code": code, "redirect_uri": redirect_uri,
            "code_verifier": verifier}
    headers = {"Accept": "application/json"}
    methods = doc.get("token_endpoint_auth_methods_supported") or ["client_secret_basic"]
    if "client_secret_basic" in methods:
        raw = f"{_form(cfg.client_id)}:{_form(cfg.client_secret)}".encode()
        headers["Authorization"] = "Basic " + base64.b64encode(raw).decode()
    else:
        data |= {"client_id": cfg.client_id, "client_secret": cfg.client_secret}
    try:
        resp = await http.post(doc["token_endpoint"], data=data, headers=headers)
    except httpx.HTTPError as exc:
        raise SSOError("the identity provider could not be reached") from exc
    body = _json(resp)
    token = body.get("id_token") if isinstance(body, dict) else None
    if resp.status_code >= 400 or not token:
        why = body.get("error_description") or body.get("error") if isinstance(body, dict) else ""
        raise SSOError(f"the identity provider refused the sign-in ({why or resp.status_code})")
    return str(token)


async def verify(http: httpx.AsyncClient, doc: dict[str, Any], cfg: Config, id_token: str,
                 *, nonce: str) -> dict[str, Any]:
    """The ID token's claims, once its signature, issuer, audience, expiry and
    nonce hold — and the address it names is one of the workspace's domains."""
    try:
        header = jwt.get_unverified_header(id_token)
    except jwt.PyJWTError as exc:
        raise SSOError("the identity provider sent something that is not a token") from exc
    alg = header.get("alg")
    if alg not in ALGORITHMS:
        raise SSOError(f"the token is signed with {alg!r}, which this service does not take")
    key = await _key(http, doc, header.get("kid"), alg)
    try:
        claims = jwt.decode(id_token, key, algorithms=[alg], audience=cfg.client_id,
                            issuer=doc["issuer"], leeway=60,
                            options={"require": ["exp", "iat", "iss", "aud", "sub"]})
    except jwt.PyJWTError as exc:
        raise SSOError(f"the identity provider's token does not hold: {exc}") from exc
    if claims.get("nonce") != nonce:
        raise SSOError("the token was not issued for this sign-in")
    email = str(claims.get("email") or claims.get("preferred_username") or "").strip().lower()
    if "@" not in email:
        raise SSOError("the identity provider did not say who signed in (no email claim)")
    if claims.get("email_verified") in (False, "false"):
        raise SSOError("the identity provider has not verified this address")
    if domain_of(email) not in cfg.domains:
        raise SSOError(f"{email} is not at a domain this workspace signs in "
                       f"({', '.join(cfg.domains)})")
    return {**claims, "email": email}


async def _key(http: httpx.AsyncClient, doc: dict[str, Any], kid: str | None, alg: str) -> Any:  # noqa: ANN401
    for fresh in (False, True):            # a key not seen yet: the provider rotated
        keys = await _jwks(http, doc["jwks_uri"], fresh=fresh)
        found = [k for k in keys if not kid or k.get("kid") == kid]
        if found:
            try:
                return jwt.PyJWK(found[0], algorithm=alg).key
            except jwt.PyJWTError as exc:
                raise SSOError(f"the provider's key cannot check this token: {exc}") from exc
    raise SSOError("the token is signed with a key the provider does not publish")


_jwks_cache: dict[str, tuple[float, list[dict[str, Any]]]] = {}


async def _jwks(http: httpx.AsyncClient, uri: str, *, fresh: bool) -> list[dict[str, Any]]:
    held = _jwks_cache.get(uri)
    if held and not fresh and time.time() - held[0] < DISCOVERY_TTL:
        return held[1]
    try:
        body = _json(await http.get(uri))
    except httpx.HTTPError as exc:
        raise SSOError("the provider's keys could not be fetched") from exc
    keys = body.get("keys") if isinstance(body, dict) else None
    if not isinstance(keys, list):
        raise SSOError("the provider publishes no keys")
    _jwks_cache[uri] = (time.time(), keys)
    return keys


def _form(value: str) -> str:
    from urllib.parse import quote

    return quote(value, safe="")


def _json(resp: httpx.Response):  # noqa: ANN202 - dict | list
    try:
        return resp.json()
    except ValueError:
        return {}
