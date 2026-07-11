"""Vendor-side license tooling (launch-readiness §5): issue licenses without
hand-writing code. Same Ed25519 + JCS stack the plane uses; fully offline.

  axor-license keygen                            → vendor keypair (hex)
  axor-license issue --key-file <path> --org …   → signed license-file JSON
      (key sources, preferred first: --key-file, AXOR_VENDOR_KEY env, --key)
  axor-license verify --pubkey <pub> <file>      → validity + fields

Commercial module (ee/) — see ee/LICENSE.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime

from axor_backend.ee.license import LicenseError, sign_license, verify_license


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
    lic = {
        "org": args.org,
        "tier": args.tier,
        "node_ceiling": args.nodes,
        "expiry": args.expiry,
        "features": args.features or [],
    }
    print(sign_license(lic, key))
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
        "status": status, "org": lic.org, "tier": lic.tier,
        "node_ceiling": lic.node_ceiling, "expiry": lic.expiry,
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
    issue.add_argument("--org", required=True)
    issue.add_argument("--tier", default="team", choices=["team", "enterprise"])
    issue.add_argument("--nodes", type=int, default=10, help="node ceiling")
    issue.add_argument("--expiry", required=True, help="ISO date, e.g. 2027-01-01")
    issue.add_argument("--features", nargs="*", default=[])
    issue.set_defaults(fn=_issue)

    verify = sub.add_parser("verify", help="verify a license file")
    verify.add_argument("--pubkey", required=True, help="vendor public key (hex)")
    verify.add_argument("file", help="license file path, or - for stdin")
    verify.set_defaults(fn=_verify)

    args = parser.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    raise SystemExit(main())
