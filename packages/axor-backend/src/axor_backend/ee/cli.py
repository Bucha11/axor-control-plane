"""Vendor-side license tooling (launch-readiness §5): issue licenses without
hand-writing code. Same Ed25519 + JCS stack the plane uses; fully offline.

  axor-license keygen                       → vendor keypair (hex)
  axor-license issue --key <priv> --org …   → signed license-file JSON
  axor-license verify --pubkey <pub> <file> → validity + fields

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


def _issue(args: argparse.Namespace) -> int:
    lic = {
        "org": args.org,
        "tier": args.tier,
        "node_ceiling": args.nodes,
        "expiry": args.expiry,
        "features": args.features or [],
    }
    print(sign_license(lic, args.key))
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
    issue.add_argument("--key", required=True, help="vendor private key (hex)")
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
