"""Product auth — local token + scoped API keys (architecture section 9).

Two credentials, both bearer tokens on the Authorization header:

- **Local token** (`AXOR_API_TOKEN`): the operator's master secret for a
  self-hosted deployment. It carries every scope. Set it and auth turns on;
  leave it unset and the backend is open (dev / the compose default), the same
  opt-in posture as `AXOR_ALLOW_UNSIGNED`.
- **Scoped API keys**: minted by the operator for connections (the proxy's
  ingest key, a read-only dashboard key). Stored hashed; a key carries a subset
  of scopes. This is the "scoped API keys for connections" half of §9.

This is access control (who may call the API), distinct from and composed with
the plane's ed25519 command signing (integrity of an operator action even
against a compromised backend, protocol §6): a caller must both be authorized
here AND present a valid signature there for a plane command to take effect.

Scopes (least-privilege): ``read`` < ``ingest`` < ``operate`` < ``admin``.
Higher scopes do not imply lower ones — a key lists exactly what it may do; the
master token is the only all-scope principal.

Two rules keep the ladder from leaking:

- **Reading can be privileged.** ``_READ_POLICY`` is consulted before the
  GET-defaults-to-read rule, so credential listing and the vault's inventory
  surfaces are gated too. A policy entry that only guards writes does not guard
  a surface whose *contents* are the secret.
- **A key may be bound to one node.** Scopes say what a credential may do;
  :attr:`Principal.node_id` says who it may do it AS. The plane checks it on
  every upstream write, so an ingest key issued to one governed node cannot
  forge its neighbour's telemetry.
"""
from __future__ import annotations

import hashlib
import hmac
import secrets
from dataclasses import dataclass

SCOPES = frozenset({"read", "ingest", "operate", "admin"})

_READ_METHODS = ("GET", "HEAD", "OPTIONS")

# Reads whose CONTENTS are privileged, matched BEFORE the "GET defaults to
# read" rule. Without this the read default silently outranks the write table
# below, and a policy entry meant to guard a surface only guards writing to it.
# Everything not listed here reads with `read`.
_READ_POLICY: tuple[tuple[str, str], ...] = (
    # Listing keys is credential enumeration: ids, scopes, labels, dates.
    ("/v1/keys", "admin"),
    # Every enrolment with the nodes allowed to dispense it — the map of which
    # node can reach which tool credential.
    ("/v1/vault/creds/health", "admin"),
    # The record of who requested a signature over what.
    ("/v1/vault/signing/audit", "admin"),
    # Pubkeys are explicitly NOT secrets (vault_signing module docstring), but
    # the per-key operator allowlist is operational config, so: not `read`.
    ("/v1/vault/signing/keys", "operate"),
)

# (prefix, suffix, scope) for writes whose parent prefix would otherwise assign
# too low a bar. Matched before _WRITE_POLICY, so a privileged sub-route is not
# swallowed by its parent's scope.
_ROUTE_POLICY: tuple[tuple[str, str, str], ...] = (
    # Adapter-side plane posts (the node reporting in / acking a one-shot) are
    # ingest — a governed node's key must be able to heartbeat. Operator actions
    # on the plane (command / facts / cascade-stop) stay operate.
    ("/v1/plane/", "/telemetry", "ingest"),
    ("/v1/plane/", "/consumed", "ingest"),
    # Minting a share link publishes an EvidenceCase behind an unauthenticated
    # read token. That is an operator decision, not part of uploading a trace —
    # otherwise the proxy's ingest key doubles as a publishing credential.
    ("/v1/runs/", "/share", "operate"),
)

# Method+path prefix -> required scope for WRITES. First match wins.
# Open paths (no auth even when enabled) are handled separately below.
_WRITE_POLICY: tuple[tuple[str, str], ...] = (
    ("/v1/ingest/", "ingest"),
    ("/v1/wrap/", "ingest"),          # code analysis, not an operational command
    ("/v1/runs/", "ingest"),          # POST .../evidence
    ("/v1/pins/", "ingest"),
    ("/v1/plane/", "operate"),        # command / facts / cascade-stop
    ("/v1/keys", "admin"),            # mint / revoke API keys
    ("/v1/regression/schedule", "operate"),  # EE schedule is operator config
    ("/v1/regression", "read"),       # replay-only, no state change
    ("/v1/replay/", "read"),          # counterfactual POST is read-only
    ("/v1/notifications/", "operate"),
    ("/v1/lab/", "operate"),          # accepting a Lab deploy changes the corpus
    # ── vault, longest prefix first: the two subsystems are not one surface ──
    # A governed node fetches its own tool credential at call time, so this is
    # a node-level read, not an operator action. Its real protection is the
    # per-node dispense scope checked inside the vault (ToolCredentialVault).
    ("/v1/vault/creds/dispense", "ingest"),
    # Narrowing during an incident — operate, like every other narrowing.
    ("/v1/vault/creds/revoke", "operate"),
    # Signing an operator command. Not admin: this is exactly the action the
    # `operate` scope names, and it is separately gated by the signing token
    # and the per-key operator allowlist, and audited unconditionally.
    ("/v1/vault/signing/sign", "operate"),
    # Everything else in either vault is custody config: enroll, rotate, and
    # creating a signing key.
    ("/v1/vault/", "admin"),
    # Verifying a license STORES and ACTIVATES it (app.license_verify), so it
    # changes the deployment's entitlement state — admin, and never open.
    ("/v1/license/verify", "admin"),
)

# Served without auth even when it is enabled — as (method, prefix) pairs, not
# bare prefixes: opening a path must not open every verb on it. `/v1/share/` is
# a deliberately scoped-by-design read surface (spec §8.3 — one case, its own
# read token); revoking one is an operator action and stays authenticated.
_OPEN_ROUTES: tuple[tuple[str, str], ...] = (
    ("GET", "/v1/healthz"),
    ("GET", "/v1/auth/status"),  # the UI queries this to decide whether to prompt
    ("GET", "/v1/share/"),
    ("HEAD", "/v1/healthz"),
    ("HEAD", "/v1/share/"),
)


def required_scope(method: str, path: str) -> str:
    if method in _READ_METHODS:
        for prefix, scope in _READ_POLICY:
            if path.startswith(prefix):
                return scope
        return "read"
    for prefix, suffix, scope in _ROUTE_POLICY:
        if path.startswith(prefix) and path.endswith(suffix):
            return scope
    for prefix, scope in _WRITE_POLICY:
        if path.startswith(prefix):
            return scope
    return "operate"  # unknown write path: fail safe to a higher bar


def is_open(method: str, path: str) -> bool:
    """Whether this exact (method, path) is served without authentication."""
    return any(
        method == open_method and path.startswith(prefix)
        for open_method, prefix in _OPEN_ROUTES
    )


# Upstream plane writes: the node reporting about ITSELF. A request to one of
# these speaks AS the node named in the path, so a node-bound key must match it.
# `/command` and `/cascade-stop` are excluded deliberately — they are operator
# actions aimed AT a node, not the node speaking, and they carry their own
# ed25519 signature check.
_SPEAKS_AS_NODE_SUFFIXES = ("/telemetry", "/facts", "/consumed", "/probe-report")
_PLANE_PREFIX = "/v1/plane/"


def plane_node_of(method: str, path: str) -> str | None:
    """The node_id a request claims to speak as, or None if it claims none.

    Only upstream plane writes speak as a node; reads and operator commands do
    not. Returns the ``{node_id}`` path segment so the caller can check it
    against a node-bound credential.
    """
    if method in _READ_METHODS or not path.startswith(_PLANE_PREFIX):
        return None
    if not path.endswith(_SPEAKS_AS_NODE_SUFFIXES):
        return None
    node_id = path[len(_PLANE_PREFIX):].rsplit("/", 1)[0]
    return node_id or None


def hash_secret(secret: str) -> str:
    return hashlib.sha256(secret.encode()).hexdigest()


def generate_key() -> tuple[str, str]:
    """Return (key_id, full_secret). The full secret is shown once; only its
    hash is stored. The key_id prefixes the secret so lookups are O(1)."""
    key_id = "ak_" + secrets.token_hex(4)
    secret = key_id + "." + secrets.token_urlsafe(24)
    return key_id, secret


@dataclass(frozen=True)
class Principal:
    kind: str  # "master" | "key" | "user"
    key_id: str
    scopes: frozenset[str]
    # set only for kind == "user" (an axor-identity login): who and which org.
    # `org` is the tenant the token is scoped to; a hosted deployment uses it to
    # partition data. None for master/key principals (operator credentials).
    org: str | None = None
    role: str | None = None
    tier: str | None = None
    user_id: str | None = None
    email: str | None = None
    # A key minted FOR ONE NODE carries its node_id, and the plane refuses to
    # let it speak for any other (see `may_speak_for`). Command signing protects
    # the downstream direction; this is the upstream half — without it any
    # ingest key can forge a neighbour's heartbeat, level or health verdict.
    # None = unbound, the pre-existing behaviour for fleet-wide operator keys.
    node_id: str | None = None

    def may(self, scope: str) -> bool:
        return scope in self.scopes

    def may_speak_for(self, node_id: str) -> bool:
        """Whether this principal may post telemetry/facts/health AS `node_id`.

        An unbound principal (the master token, an operator key, a human login)
        speaks for the whole fleet. A node-bound key speaks only for its own
        node — so a compromised node cannot report on behalf of its neighbours.
        """
        return self.node_id is None or self.node_id == node_id


MASTER = "master"

# An axor-identity role grants a SET of scopes (least-privilege ladder). A human
# who logs in never mints API keys unless they own/administer the org.
ROLE_SCOPES: dict[str, frozenset[str]] = {
    "viewer": frozenset({"read"}),
    "member": frozenset({"read", "ingest"}),
    "admin": frozenset({"read", "ingest", "operate"}),
    "owner": frozenset({"read", "ingest", "operate", "admin"}),
}


def scopes_for_role(role: str) -> frozenset[str]:
    return ROLE_SCOPES.get(role, frozenset())


def master_principal() -> Principal:
    return Principal(kind=MASTER, key_id="master", scopes=frozenset(SCOPES))


def constant_time_eq(a: str, b: str) -> bool:
    return hmac.compare_digest(a, b)
