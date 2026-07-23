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
"""
from __future__ import annotations

import hashlib
import hmac
import secrets
from dataclasses import dataclass

SCOPES = frozenset({"read", "ingest", "operate", "admin"})

# Method+path prefix -> required scope. First match wins; GET defaults to read.
# Open paths (no auth even when enabled) are handled separately below.
_WRITE_POLICY: tuple[tuple[str, str], ...] = (
    ("/v1/ingest/", "ingest"),
    ("/v1/wrap/", "ingest"),          # code analysis, not an operational command
    ("/v1/runs/", "ingest"),          # POST .../evidence
    ("/v1/pins/", "ingest"),
    ("/v1/plane/", "operate"),        # command / facts / consumed / telemetry
    ("/v1/keys", "admin"),            # mint / list API keys
    ("/v1/regression/schedule", "operate"),  # EE schedule is operator config
    ("/v1/regression", "read"),       # replay-only, no state change
    ("/v1/replay/", "read"),          # counterfactual POST is read-only
    ("/v1/notifications/", "operate"),
    ("/v1/lab/", "operate"),          # accepting a Lab deploy changes the corpus
)

# Paths served without auth even when enabled: liveness, the deliberately
# scoped-by-design share links (spec §8.3 — one case, its own read token), and
# license verification (a pure utility over user-supplied input).
_OPEN_PREFIXES: tuple[str, ...] = (
    "/v1/healthz",
    "/v1/auth/status",   # the UI queries this to decide whether to prompt
    "/v1/share/",
    "/v1/license/verify",
)


def required_scope(method: str, path: str) -> str:
    if method in ("GET", "HEAD", "OPTIONS"):
        return "read"
    # Adapter-side plane posts (the node reporting in / acking a one-shot) are
    # ingest — the proxy's key must be able to heartbeat. Operator actions on
    # the plane (command / facts / cascade-stop) stay operate.
    if path.startswith("/v1/plane/") and path.endswith(("/telemetry", "/consumed")):
        return "ingest"
    for prefix, scope in _WRITE_POLICY:
        if path.startswith(prefix):
            return scope
    return "operate"  # unknown write path: fail safe to a higher bar


def is_open(path: str) -> bool:
    return any(path.startswith(p) for p in _OPEN_PREFIXES)


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
    kind: str  # "master" | "key"
    key_id: str
    scopes: frozenset[str]

    def may(self, scope: str) -> bool:
        return scope in self.scopes


MASTER = "master"


def master_principal() -> Principal:
    return Principal(kind=MASTER, key_id="master", scopes=frozenset(SCOPES))


def constant_time_eq(a: str, b: str) -> bool:
    return hmac.compare_digest(a, b)
