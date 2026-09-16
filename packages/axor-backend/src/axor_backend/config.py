"""Deployment configuration, resolved once.

Every ``AXOR_*`` variable the backend reads is resolved here, at startup, into
one frozen object — except for a short, deliberate list of modules that read
their own (``limits``, ``monitor``, ``observability``), which
``test_env_surface`` enumerates and holds to the same documentation rule.
Without that test this sentence was simply false: ten of twenty-three variables
were read elsewhere, and twelve of them had fallen out of ``.env.example``,
which is the exact drift the second reason below is about.

Two reasons, both of them things that bit us:

- **Reading the environment mid-request is a lie about when a setting takes
  effect.** The license verifier used to read ``AXOR_VENDOR_PUBKEY`` per call,
  so the pinned vendor key could differ between two requests to the same
  process. Entitlement config is deployment config; it is fixed at boot.
- **A setting nobody can enumerate cannot be documented.** With the reads
  scattered across a 1300-line factory, ``.env.example`` was maintained by
  memory. Here the dataclass fields *are* the list.

Explicit arguments to :func:`AppConfig.resolve` win over the environment, and
``None`` means "not supplied" — passing ``operator_keys={}`` really does mean
an empty keyring, not "go look at ``AXOR_OPERATOR_KEYS``".
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from axor_backend import env

DEFAULT_DATABASE_URL = "sqlite+aiosqlite:///./axor.db"
DEFAULT_SCHEDULE_SWEEP_SECONDS = 60.0
DEFAULT_IDENTITY_ISSUER = "axor-identity"


@dataclass(frozen=True)
class AppConfig:
    """One deployment's settings. Frozen: nothing reconfigures a live process."""

    database_url: str = DEFAULT_DATABASE_URL
    # ── integrity: ed25519 operator keys for plane commands (protocol §6) ────
    operator_keys: dict[str, str] | None = None
    allow_unsigned: bool = False
    # ── access control (architecture §9) ─────────────────────────────────────
    api_token: str | None = None
    identity_jwks: dict[str, Any] | None = None
    # Where the JWKS came from, when it came from a URL. Kept so the process can
    # refetch after a key rotation instead of 401-ing every login until a
    # restart (identity_client.JwksRefresher). None when supplied inline.
    identity_jwks_url: str | None = None
    # The `iss` claim an access token must carry. Configurable because a
    # deployment may run its own identity under its own name — it had no
    # environment variable at all while every setting beside it did, so such a
    # deployment could not be configured to log anyone in.
    identity_issuer: str = "axor-identity"
    # ── the vault's two separate credentials (spec v2 Ch.5 §3) ───────────────
    vault_creds_token: str | None = None
    vault_signing_token: str | None = None
    # ── housekeeping ─────────────────────────────────────────────────────────
    retention_days: float | None = None
    schedule_sweep_seconds: float = DEFAULT_SCHEDULE_SWEEP_SECONDS
    # ── entitlement (monetization §4) ────────────────────────────────────────
    vendor_pubkey: str = ""
    env_license: str | None = None
    # The organization this deployment is licensed to. A license names the org
    # it was issued to and that name is signed, but nothing compared it to
    # anything, so any vendor-signed license activated anywhere. Set it and a
    # license issued to someone else is refused; leave it unset on a
    # single-tenant install and boot says so, the same opt-in posture as
    # `allow_unsigned`.
    org: str = ""
    # Where to FETCH a renewed license, when the vendor runs one. Optional and
    # off by default; unset keeps the manual paste flow, which is the only one
    # an air-gapped deployment can have.
    license_renewal_url: str = ""
    # Whether this deployment REPORTS its governed-node usage to the vendor.
    # Deliberately its own switch and not implied by `license_renewal_url`:
    # renewal is the vendor answering a question about the deployment, usage
    # reporting is the deployment volunteering something about the customer, and
    # a customer who wanted the first has not thereby agreed to the second.
    usage_reporting: bool = False
    # ── egress ───────────────────────────────────────────────────────────────
    webhook_block_private: bool = False

    @property
    def auth_enabled(self) -> bool:
        """Auth is enforced iff a master token is configured. Unset = open, the
        same opt-in posture as ``allow_unsigned`` (architecture §9)."""
        return self.api_token is not None

    @property
    def identity_enabled(self) -> bool:
        """Whether a human can authenticate with an axor-identity access token.
        Its presence is what makes a deployment multi-tenant in practice."""
        return self.identity_jwks is not None

    @classmethod
    def resolve(
        cls,
        *,
        database_url: str | None = None,
        operator_keys: dict[str, str] | None = None,
        allow_unsigned: bool | None = None,
        api_token: str | None = None,
        retention_days: float | None = None,
        vault_creds_token: str | None = None,
        vault_signing_token: str | None = None,
        identity_jwks: dict[str, Any] | None = None,
        identity_issuer: str | None = None,
        vendor_pubkey: str | None = None,
        org: str | None = None,
        license_renewal_url: str | None = None,
        usage_reporting: bool | None = None,
    ) -> AppConfig:
        """Argument, else environment, else default — field by field."""
        if operator_keys is None:
            operator_keys = env.json_object("AXOR_OPERATOR_KEYS")
        if allow_unsigned is None:
            allow_unsigned = env.flag("AXOR_ALLOW_UNSIGNED")
        if api_token is None:
            api_token = env.text("AXOR_API_TOKEN") or None
        if retention_days is None:
            retention_days = env.number("AXOR_RETENTION_DAYS")
        if identity_jwks is None:
            identity_jwks = _identity_jwks()
        return cls(
            database_url=(
                database_url
                or env.text("AXOR_DATABASE_URL")
                or DEFAULT_DATABASE_URL
            ),
            operator_keys=operator_keys,
            allow_unsigned=allow_unsigned,
            api_token=api_token,
            identity_jwks=identity_jwks,
            identity_jwks_url=env.text("AXOR_IDENTITY_JWKS_URL") or None,
            identity_issuer=(
                identity_issuer
                if identity_issuer is not None
                else env.text("AXOR_IDENTITY_ISSUER") or DEFAULT_IDENTITY_ISSUER
            ),
            vault_creds_token=(
                vault_creds_token or env.text("AXOR_VAULT_CREDS_TOKEN") or None
            ),
            vault_signing_token=(
                vault_signing_token or env.text("AXOR_VAULT_SIGNING_TOKEN") or None
            ),
            retention_days=retention_days,
            # `or DEFAULT` ate a deliberate 0 along with an unset variable, so a
            # sweep interval of zero silently became sixty seconds. The default
            # belongs to the reader, which can tell absent from set.
            schedule_sweep_seconds=env.number(
                "AXOR_SCHEDULE_SWEEP_SECONDS",
                DEFAULT_SCHEDULE_SWEEP_SECONDS, minimum=0.001,
            ),
            org=(org if org is not None else env.text("AXOR_ORG")),
            usage_reporting=(
                usage_reporting
                if usage_reporting is not None
                else env.flag("AXOR_USAGE_REPORTING")
            ),
            license_renewal_url=(
                license_renewal_url
                if license_renewal_url is not None
                else env.text("AXOR_LICENSE_RENEWAL_URL")
            ),
            vendor_pubkey=(
                vendor_pubkey
                if vendor_pubkey is not None
                else env.text("AXOR_VENDOR_PUBKEY")
            ),
            env_license=env.text("AXOR_LICENSE") or None,
            # A multi-tenant server blocks webhooks aimed at internal addresses:
            # there an org admin holds `operate` without being the infrastructure
            # operator. A single-tenant self-hosted server does not, because
            # dialing its own collector on the compose network is the normal
            # case. Either way the metadata-service range is refused
            # (notifications.check_webhook_url).
            webhook_block_private=(
                identity_jwks is not None or env.flag("AXOR_WEBHOOK_BLOCK_PRIVATE")
            ),
        )


def _identity_jwks() -> dict[str, Any] | None:
    """The identity JWKS, supplied inline or fetched once at boot. Requires the
    ``axor-backend[identity]`` extra when a URL is used."""
    raw = env.text("AXOR_IDENTITY_JWKS")
    if raw:
        return env.json_object("AXOR_IDENTITY_JWKS")
    url = env.text("AXOR_IDENTITY_JWKS_URL")
    if url:
        from axor_backend.identity_client import fetch_jwks

        return fetch_jwks(url)
    return None
