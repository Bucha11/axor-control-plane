"""Vendor-side license tooling (launch-readiness §5): issue licenses without
hand-writing code. Same Ed25519 + JCS stack the plane uses; fully offline.

  axor-license keygen                            → vendor keypair (hex)
  axor-license issue --key-file <path> --org …   → signed license-file JSON
      (key sources, preferred first: --key-file, AXOR_VENDOR_KEY env, --key)
      prints the customer's .env block alongside, so the organization name is
      copied rather than retyped — it must match `AXOR_ORG` EXACTLY or the
      deployment refuses the license, and it is a free-text company name.
  axor-license verify --pubkey <pub> <file>      → validity + fields

Commercial module (ee/) — see ee/LICENSE.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime

from axor_backend.ee.license import KNOWN_MODULES, LicenseError, sign_license, verify_license


def _keygen(_args: argparse.Namespace) -> int:
    from nacl.signing import SigningKey

    key = SigningKey.generate()
    print(json.dumps({
        "vendor_private_key": bytes(key).hex(),
        "vendor_public_key": key.verify_key.encode().hex(),
        "note": "keep the private key offline; ship/pin the public key "
                "(AXOR_VENDOR_PUBKEY) with the distribution",
    }, indent=2))
    return 0


def _resolve_key(args: argparse.Namespace) -> str | None:
    """Private key, most-hygienic source first: --key-file, then
    AXOR_VENDOR_KEY, then --key. An argv key lands in shell history and `ps`
    output — supported for compat, discouraged in help."""
    import os

    if args.key_file:
        return open(args.key_file).read().strip()
    return os.environ.get("AXOR_VENDOR_KEY") or args.key


def _issue(args: argparse.Namespace) -> int:
    key = _resolve_key(args)
    if not key:
        print(
            "no signing key: pass --key-file, set AXOR_VENDOR_KEY, or --key",
            file=sys.stderr,
        )
        return 2
    if args.control_plane and args.governed_nodes <= 0:
        # The ceiling used to be decorative, so a zero passed unnoticed. It is
        # checked on every housekeeping sweep now, and a Control Plane license
        # with a ceiling of zero means a warning on every pass, for a customer
        # who has paid. It never blocks a node — it just accuses one.
        print(
            "--control-plane needs a --governed-nodes ceiling above 0: the "
            "deployment compares its live fleet against it on every sweep, so "
            "0 warns forever about a customer who has paid.",
            file=sys.stderr,
        )
        return 2
    lic = {
        "organization": args.org,
        "workspace_tier": args.workspace_tier,
        "modules": {
            "private_lab": args.private_lab,
            "control_plane": args.control_plane,
        },
        "governed_node_ceiling": args.governed_nodes,
        "self_hosted_runner": args.self_hosted,
        "expires_at": args.expires_at,
        "features": args.features or [],
    }
    signed = sign_license(lic, key)
    print(signed)
    if not args.no_env_block:
        from nacl.signing import SigningKey  # noqa: PLC0415

        pub = SigningKey(bytes.fromhex(key)).verify_key.encode().hex()
        print(
            "\n# ── send this to the customer with the license file ──\n"
            f"# AXOR_ORG must match the license EXACTLY or it is refused.\n"
            f"AXOR_ORG={args.org}\n"
            f"AXOR_VENDOR_PUBKEY={pub}\n"
            f"# expires {args.expires_at} — EE goes read-only after that date;\n"
            "# safety features are untouched and never require a license.",
            file=sys.stderr,
        )
    return 0


def _verify(args: argparse.Namespace) -> int:
    raw = sys.stdin.read() if args.file == "-" else open(args.file).read()
    try:
        lic = verify_license(raw, args.pubkey)
    except LicenseError as exc:
        print(f"INVALID: {exc}", file=sys.stderr)
        return 1
    today = datetime.now(UTC).date().isoformat()
    status = "EXPIRED (EE read-only; safety unaffected)" if lic.is_expired(today) else "VALID"
    print(json.dumps({
        "status": status,
        "organization": lic.organization,
        "workspace_tier": lic.workspace_tier,
        "modules": {m: lic.has_module(m) for m in KNOWN_MODULES},
        "governed_node_ceiling": lic.governed_node_ceiling,
        "self_hosted_runner": lic.self_hosted_runner,
        "expires_at": lic.expires_at,
        "features": list(lic.features),
    }, indent=2))
    return 0 if status == "VALID" else 2


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="axor-license")
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("keygen", help="generate a vendor Ed25519 keypair").set_defaults(fn=_keygen)

    issue = sub.add_parser("issue", help="sign a license file")
    issue.add_argument(
        "--key-file",
        help="path to a file holding the vendor private key (hex) — preferred",
    )
    issue.add_argument(
        "--key",
        help="vendor private key (hex) on argv — lands in shell history; "
             "prefer --key-file or the AXOR_VENDOR_KEY env var",
    )
    issue.add_argument("--org", required=True, help="organization name")
    issue.add_argument(
        "--workspace-tier", default="team", choices=["community", "team", "security"],
        help="Private Lab workspace tier (axor-packaging.md §1)",
    )
    issue.add_argument(
        "--private-lab", action=argparse.BooleanOptionalAction, default=True,
        help="enable the Private Lab module (on by default for paid tiers)",
    )
    issue.add_argument(
        "--control-plane", action=argparse.BooleanOptionalAction, default=False,
        help="enable the Control Plane production-governance module (add-on)",
    )
    issue.add_argument(
        "--governed-nodes", type=int, default=0,
        help="governed-node ceiling (Control Plane); must be > 0 when "
             "--control-plane is set, 0 when the module is off",
    )
    issue.add_argument(
        "--no-env-block", action="store_true",
        help="suppress the customer .env block printed on stderr",
    )
    issue.add_argument(
        "--self-hosted", action=argparse.BooleanOptionalAction, default=False,
        help="license a self-hosted / VPC runner",
    )
    issue.add_argument("--expires-at", required=True, help="ISO date, e.g. 2027-01-01")
    issue.add_argument(
        "--features", nargs="*", default=[],
        help="granular EE flags (e.g. sso rbac compliance_exports)",
    )
    issue.set_defaults(fn=_issue)

    verify = sub.add_parser("verify", help="verify a license file")
    verify.add_argument("--pubkey", required=True, help="vendor public key (hex)")
    verify.add_argument("file", help="license file path, or - for stdin")
    verify.set_defaults(fn=_verify)

    args = parser.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    raise SystemExit(main())
