# TODO — the vendor licensing service (not in this repo, and not by accident)

**Status: not built. Nothing here depends on it; auto-renewal for self-hosted
customers does.** Everything else in the money path — the meter, the invoice,
the license format, the CLI that signs one, the renewal client that fetches
one — is built and tested in this repository. This file is the other end of
the wire, so it does not get lost.

## What it is

One HTTP endpoint that holds the vendor's Ed25519 **private** key and answers
"here is this customer's current license". Under a hundred lines. No database
of its own: the state lives in Stripe and in axor-identity.

## Why it cannot live in this repo

Two reasons, and the second is the one that decides it.

**axor-identity ships to customers.** It is in `docker-compose.yml` beside
postgres, backend, proxy and frontend — the same image with a different
entrypoint. A self-hosted customer runs `docker compose up` and gets their own
identity service with their own JWT signing key. So identity is not "the
vendor's service": in hosted it is yours, in self-hosted it is theirs, and it
is the same code. Putting the vendor key in it leaves one environment variable
between the key that mints every customer's license and a customer's machine.
Not impossible — a key is a secret, not code, and a self-hosted customer simply
never sets it — but the whole protection is that nobody ever copies the wrong
`.env`.

**Identity cannot answer the question anyway.** Signing a license needs "have
they paid?", and that is Stripe's answer, not identity's. Identity knows the
`tier` a customer is *entitled* to; Stripe knows whether the subscription is
*current*. A license is the intersection, plus a date. Wiring the payment
processor and the signing key into the authentication service makes it two
products, and the worse of the two is the one holding the key.

## The contract this repo already implements

`axor_backend.licensing._fetch_license` POSTs to `AXOR_LICENSE_RENEWAL_URL`
inside the last **21 days** of the current license (`RENEWAL_WINDOW_DAYS`), once
per housekeeping sweep, per tenant.

**Request:**

```json
{
  "organization": "Acme Corp",
  "expires_at": "2026-10-01",
  "org": "public",
  "usage": {
    "month": "2026-08",
    "workspace_tier": "team",
    "included_nodes": 10,
    "peak_nodes": 14,
    "peak_day": "2026-08-12",
    "distinct_nodes": 20,
    "billable_nodes": 4
  }
}
```

`organization` and `expires_at` are the license **in force**. It is
vendor-signed and names the organization, so it identifies the caller on its
own — the deployment holds no other credential for this call, and needs none.

`usage` is present **only** when the operator set `AXOR_USAGE_REPORTING`, which
is deliberately a separate switch from the renewal URL: renewal is the vendor
answering a question about the deployment, usage is the deployment volunteering
something about the customer. The key is omitted entirely when reporting is
off, so "not reported" is distinguishable from "reported as nothing". It is
counts only, for the last **closed** month, and never node ids.

**Response:** `{"license_json": "<the license file, verbatim>"}` — or **204**
for "nothing newer".

**What the deployment does with it** (`renewal_rejection`): verifies the
signature against its pinned `AXOR_VENDOR_PUBKEY`, checks the license is issued
to this deployment's organization, checks it is not already expired, and
refuses any license whose `expires_at` is **earlier** than the one in force.
That last check is what makes an unauthenticated endpoint safe: the only thing
it can do is move a deployment forward.

Anything else — unreachable, bad signature, wrong organization, stale file —
leaves the license in force untouched and lets the deployment degrade on its
own schedule, as if renewal had never been configured. A vendor outage never
costs a paying customer their entitlement.

## What it has to do

```
1. Stripe: is this organization's subscription current?
      no  -> 204
2. tier + node ceiling: from axor-identity's orgs table, or Stripe metadata
3. sign:  axor-license issue --key-file <vendor.key> \
              --org "<organization>" \
              --workspace-tier <tier> \
              [--governed-nodes <negotiated>] \
              --expires-at <end of the paid period>
      (the ceiling defaults to the rung's allowance —
       community 1, team 10, security 50; enterprise is negotiated
       and must be passed)
4. usage: bill `billable_nodes` at the rung's overage rate
      team $75/node/mo, security $50/node/mo,
      community free, enterprise contracted
5. return {"license_json": ...}
```

**Set `expires_at` to the end of the paid period, not a year out.** The check
is offline: a cancelled subscription cannot be revoked, so a license outlives
the money by exactly however long it was written for. Monthly subscription ⇒
monthly license.

## What works today without it

- **Issue by hand:** `axor-license issue` on a machine that is not in the
  customer's compose file. Prints the license file and the customer's `.env`
  block (`AXOR_ORG`, `AXOR_VENDOR_PUBKEY`).
- **Hosted:** cron `POST /v1/license/verify` with `org=<tenant>` — the operator
  can license any tenant, and the license still has to be issued to it.
- **Self-hosted:** leave `AXOR_LICENSE_RENEWAL_URL` unset. The manual paste
  flow is unchanged and is the only one an air-gapped install can have.
- **Expiry is no longer silent:** the `license_expiring` notification fires at
  30/14/7/3/1 days, so a lapse is seen before it happens.

The only thing missing is a self-hosted customer not having to swap a file each
period.

## When to build it

When there is a self-hosted customer for whom that is a real annoyance. There
is no reason to operate a key-holding service for zero of them, and the
contract above is fixed, so the Control Plane will not need touching when it
appears.
