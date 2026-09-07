"""Sink-side credential injection (ui-spec §14.2, spec v2 Ch.5 §1).

This module is the one place the proxy breaks its own first rule, and it does so
deliberately and narrowly. Section 6 says auth is passthrough, byte-for-byte —
the proxy never parses, substitutes or stores credentials — and §14.2 says vault
mode is the reversal of exactly that, names the price ("the proxy now holds and
injects credentials, becoming a high-value target") and bounds it:

* **A mode, never the default.** Opt-in per tool, and the opt-in is the PROXY
  operator's (``AXOR_VAULT_TOOLS``), not the vault's. If enrolling a credential
  were enough to switch a tool into vault mode, a change on the plane would turn
  off passthrough for a proxy whose operator never agreed to it. Section 6 stays
  literally true for every tool not in that list.
* **Nothing is stored, and nothing is cached.** The credential is fetched at
  call time and lives in one request object. Decision #14 forbids a TTL cache in
  terms: it "would reintroduce the secret-on-proxy this feature exists to
  remove".
* **Fail closed, federation-wide** (decision #14). Vault unreachable, credential
  missing, revoked, or out of this node's scope → a typed denial and NO upstream
  call. A deny is enforcement working; availability is the vault's problem.
* **The endpoint is not the agent's to choose.** The (tool, endpoint) pair sent
  to the vault comes from the proxy's own tool table, so a prompt-injected agent
  redirecting a call at attacker.example is asking for a credential enrolled
  against a different endpoint, and is refused. Scope is operator config,
  inaccessible from runtime reads.

What the agent sees is what §14.2 is for: a credential it never held cannot be
exfiltrated by anything it says. The proxy REPLACES the injection header rather
than adding to it — an agent-supplied Authorization on a vault-mode tool is
discarded, because "the agent's config holds vault references, never keys".
"""
from __future__ import annotations

import os
from dataclasses import dataclass

import httpx


class CredentialDenied(Exception):
    """Typed denial. Carries the reason the call was refused, never a secret."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True)
class Credential:
    secret: str
    version: int
    header: str
    scheme: str

    def applied_to(self, headers: list[tuple[bytes, bytes]]) -> list[tuple[bytes, bytes]]:
        """The forwarded headers with this credential in place.

        REPLACES any inbound header of the same name: on a vault-mode tool the
        agent's own value is not a fallback, it is the thing being removed.
        """
        name = self.header.encode()
        lowered = name.lower()
        value = f"{self.scheme} {self.secret}".strip().encode()
        return [*[(k, v) for k, v in headers if k.lower() != lowered], (name, value)]


def vault_tools(raw: str | None = None) -> frozenset[str]:
    """Tools this proxy injects credentials for. Empty = pure passthrough."""
    value = raw if raw is not None else os.environ.get("AXOR_VAULT_TOOLS", "")
    return frozenset(t.strip() for t in value.split(",") if t.strip())


class CredentialVault:
    """Dispense client. One call, one credential, no memory of it."""

    def __init__(
        self,
        backend_url: str,
        ingest_key: str | None = None,
        client: httpx.AsyncClient | None = None,
        creds_token: str | None = None,
        timeout: float = 10.0,
    ) -> None:
        self._base = backend_url.rstrip("/")
        self._client = client
        self._timeout = timeout
        self._headers: dict[str, str] = {}
        if ingest_key:
            # A node-bound `ingest` key: the plane refuses a key bound to one
            # node that asks for another's credential (routers/vault._speaking_as).
            self._headers["Authorization"] = f"Bearer {ingest_key}"
        token = (
            creds_token if creds_token is not None
            else os.environ.get("AXOR_VAULT_CREDS_TOKEN", "")
        )
        if token:
            self._headers["X-Vault-Creds-Token"] = token

    async def dispense(self, node_id: str, tool: str, endpoint: str) -> Credential:
        """Fetch the credential for this exact (tool, endpoint), or refuse.

        Every failure is a `CredentialDenied` — a refused dispense, an
        unreachable vault and a malformed answer are the same thing to the
        caller: no credential, so no call.
        """
        client = self._client or httpx.AsyncClient(timeout=self._timeout)
        owns = self._client is None
        try:
            response = await client.post(
                f"{self._base}/v1/vault/creds/dispense",
                json={"node_id": node_id, "tool": tool, "endpoint": endpoint},
                headers=self._headers,
            )
        except httpx.HTTPError as exc:
            raise CredentialDenied(
                f"credential vault unreachable ({type(exc).__name__}); "
                f"fail-closed, {tool} was not called"
            ) from exc
        finally:
            if owns:
                await client.aclose()
        if response.status_code != 200:
            detail = _detail(response)
            raise CredentialDenied(
                f"vault refused the credential for ({tool}, {endpoint}): {detail}"
            )
        try:
            body = response.json()
            return Credential(
                secret=str(body["secret"]),
                version=int(body["version"]),
                header=str(body.get("header") or "Authorization"),
                scheme=str(body.get("scheme", "Bearer")),
            )
        except (ValueError, KeyError, TypeError) as exc:
            raise CredentialDenied(
                f"vault answered something that is not a credential: {exc}"
            ) from exc


def _detail(response: httpx.Response) -> str:
    """The vault's own reason, or the status. Never the response body verbatim —
    a credential surface's error text is not something to relay wholesale."""
    try:
        detail = response.json().get("detail")
    except ValueError:
        detail = None
    return str(detail) if detail else f"HTTP {response.status_code}"
