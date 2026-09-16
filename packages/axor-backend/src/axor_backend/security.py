"""Who is calling, and may they.

Two things live here, in the order a request meets them:

1. :func:`resolve_principal` — turn a bearer token into a
   :class:`~axor_backend.auth.Principal`, or ``None``. Three credential kinds
   are tried in descending privilege: the operator's master token, a scoped
   API key, an axor-identity access token.
2. :func:`auth_middleware` — the four gates every request passes: is the route
   open, is the caller authenticated, does the caller hold the required scope,
   and (for upstream plane writes) may the caller speak as the node it names.
   Then it stamps the request's tenant.

The *policy* — which scope a route needs, which routes are open, which routes
speak as a node — is in :mod:`axor_backend.auth`, deliberately as pure data.
This module is the machinery that applies it; keeping them apart is what makes
the policy table reviewable on its own.
"""
from __future__ import annotations

import logging
from collections.abc import Callable

from fastapi import Request, Response
from fastapi.responses import JSONResponse

from axor_backend import auth as auth_mod
from axor_backend.auth import (
    Principal,
    hash_secret,
    is_open,
    master_principal,
    policy_path,
    required_scope,
)
from axor_backend.tenancy import set_current_org

log = logging.getLogger("axor.backend.auth")


def _path_of(request: Request) -> str:
    """The canonical path every policy decision in this module is made on.

    Never `request.url.path` directly: that is the path as the client wrote it,
    including any mount prefix the router strips before matching routes. See
    `auth.policy_path` for what went wrong when the two disagreed.
    """
    return policy_path(request.url.path, request.scope.get("root_path", ""))


async def resolve_principal(request: Request) -> Principal | None:
    """Bearer from the Authorization header, or ``?token=`` on the few routes a
    browser opens directly. Returns the principal, or None if unauthenticated.
    """
    config = request.app.state.config
    token = None
    header = request.headers.get("authorization", "")
    if header.lower().startswith("bearer "):
        token = header[7:].strip()
    if token is None and auth_mod.accepts_query_token(
        request.method, _path_of(request)
    ):
        # A token in the query string ends up in access logs, browser history
        # and Referer headers, so it is accepted ONLY where a header is
        # genuinely impossible: EventSource cannot set one, and neither can an
        # <a download> link. Every other route requires the Authorization
        # header — previously any route accepted ?token=, which meant a copied
        # URL could carry a live credential anywhere.
        token = request.query_params.get("token")
    if not token:
        return None
    if config.api_token and auth_mod.constant_time_eq(token, config.api_token):
        return master_principal()
    # API key: the key_id prefixes the secret (ak_xxx.yyy).
    key_id = token.split(".", 1)[0]
    record = await request.app.state.store.get_api_key(key_id)
    if record and auth_mod.constant_time_eq(
        record["hashed_secret"], hash_secret(token)
    ):
        # A key carries the org it was minted under, so the connection using it
        # reads and writes that org's data (None → the public tenant), and the
        # node it was minted FOR, if any.
        return Principal(
            kind="key",
            key_id=key_id,
            scopes=frozenset(record["scopes"]),
            org=record.get("org_id"),
            node_id=record.get("node_id"),
        )
    # axor-identity login: a human's access token, verified locally against the
    # JWKS. The org scopes the principal to a tenant; the role maps to the scope
    # ladder (a viewer reads, an owner may mint keys).
    jwks = getattr(request.app.state, "jwks", None)
    if jwks is not None:
        from axor_backend.identity_client import (
            IdentityError,
            UnknownKeyId,
            verify_access_token,
        )

        def verify(document: dict) -> object:
            return verify_access_token(
                token, document, issuer=config.identity_issuer
            )

        try:
            claims = verify(jwks.document)
        except UnknownKeyId:
            # The one failure a refetch can fix: identity rotated its signing
            # key and this process still holds the document from before. Every
            # login used to 401 from that moment until somebody restarted the
            # backend. Rate-limited inside the refresher, because presenting a
            # token needs no credential.
            document = await jwks.refreshed()
            if document is None:
                return None
            try:
                claims = verify(document)
            except IdentityError:
                return None
        except IdentityError:
            return None
        return Principal(
            kind="user",
            key_id=claims.user_id,
            scopes=auth_mod.scopes_for_role(claims.role),
            org=claims.org,
            role=claims.role,
            user_id=claims.user_id,
            email=claims.email,
        )
    return None


async def auth_middleware(request: Request, call_next: Callable) -> Response:
    """The gate. Open posture and open routes pass straight through."""
    config = request.app.state.config
    path = _path_of(request)
    if not config.auth_enabled or is_open(request.method, path):
        return await call_next(request)
    principal = await resolve_principal(request)
    if principal is None:
        # Refusals were returned to the caller and to nobody else: a deployment
        # could not tell that it was being probed with a bad token, or that a
        # key was being used past its scope. The credential itself never
        # appears — the principal's id does, which is the point of recording it.
        log.warning("401 %s %s: no usable credential", request.method, path)
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    need = required_scope(request.method, path)
    if not principal.may(need):
        log.warning(
            "403 %s %s: %s %s holds %s, needs %s",
            request.method, path, principal.kind, principal.key_id,
            sorted(principal.scopes), need,
        )
        return JSONResponse(
            {"error": "forbidden", "need": need, "have": sorted(principal.scopes)},
            status_code=403,
        )
    # A node-bound key may only speak AS its own node (auth.Principal): scopes
    # say what a credential may do, this says who it may do it as.
    spoke_for = auth_mod.plane_node_of(request.method, path)
    if spoke_for is not None and not principal.may_speak_for(spoke_for):
        log.warning(
            "403 %s %s: %s %s is bound to node %r and may not speak as %r",
            request.method, path, principal.kind, principal.key_id,
            principal.node_id, spoke_for,
        )
        return JSONResponse(
            {
                "error": "forbidden",
                "detail": f"key {principal.key_id} is bound to node "
                f"{principal.node_id!r} and may not post as {spoke_for!r}",
            },
            status_code=403,
        )
    request.state.principal = principal
    # Scope every store query in this request to the principal's org (the public
    # tenant for master/keyless/open deployments) — see tenancy.py.
    set_current_org(principal.org)
    return await call_next(request)
