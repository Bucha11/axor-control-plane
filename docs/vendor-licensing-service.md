# TODO — the vendor licensing service (not in this repo, and not by accident)

**Status: being built as `axor-billing`, on Paddle (Paddle Billing, the
merchant of record). Nothing here depends on it; card payment and auto-renewal
do.** Everything else in the money path — the meter, the invoice, the license
format, the CLI that signs one, the renewal client that fetches one — is built
and tested in this repository. This file is the other end of the wire, so it
does not get lost.

## What it is

A small HTTP service that holds the vendor's Ed25519 **private** key and
answers "here is this customer's current license", fed by Paddle webhooks. Its
only state is which Paddle subscription belongs to which organization and which
months it has already charged; whether a subscription is paid lives in Paddle,
the tier a workspace is entitled to in axor-identity.

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
they paid?", and that is Paddle's answer, not identity's. Identity knows the
`tier` a customer is *entitled* to; Paddle knows whether the subscription is
*current*. A license is the intersection, plus a date. Wiring the payment
processor and the signing key into the authentication service makes it two
products, and the worse of the two is the one holding the key.

## The contract this repo already implements

`axor_backend.licensing._fetch_license` POSTs to `AXOR_LICENSE_RENEWAL_URL`
inside the last **21 days** of the current license (`RENEWAL_WINDOW_DAYS`), once
per entitlement sweep, per tenant. That sweep is `lifecycle.license_loop`,
hourly, always running — it is not conditional on `AXOR_RETENTION_DAYS`, which
is a separate setting it once shared a task with. Point
`AXOR_LICENSE_RENEWAL_URL` at **https**: the request above names the
organization and, with `AXOR_USAGE_REPORTING` on, its node counts; the reply is
signature-checked either way, so the exposure is what leaves, not what
arrives. Boot warns when it is not https.

**Request:**

```json
{
  "organization": "Acme Corp",
  "expires_at": "2026-10-01",
  "org": "public",
  "license_json": "<the license file in force, verbatim>",
  "usage": {
    "month": "2026-08",
    "workspace_tier": "team",
    "included_nodes": 10,
    "peak_nodes": 14,
    "peak_day": "2026-08-12",
    "distinct_nodes": 20,
    "billable_nodes": 4,
    "included_developers": 0,
    "active_developers": 7,
    "billable_developers": 7
  }
}
```

`organization` and `expires_at` are the license **in force**, and
`license_json` is that license itself, vendor-signed. The vendor answers only
when that file verifies and names the organization asked about: a bare name
proves nothing, and a vendor that renewed on one would hand any stranger who
knows it that organization's paid license — and bill the organization for the
usage the stranger reported. The deployment holds no other credential for this
call, and needs none.

`usage` is present **only** when the operator set `AXOR_USAGE_REPORTING`, which
is deliberately a separate switch from the renewal URL: renewal is the vendor
answering a question about the deployment, usage is the deployment volunteering
something about the customer. The key is omitted entirely when reporting is
off, so "not reported" is distinguishable from "reported as nothing". It is
counts only, for the last **closed** month, and never node or developer ids —
governed nodes and the developers whose coding agents ran (as the invoice
counts them: past the seat cap is not billed).

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

One Paddle subscription per organization, monthly, opened by Paddle checkout
with `customData: {"org": "<org_id>"}` (Paddle copies it onto the
subscription). Its recurring price is the rung's base — $0 for Coding and
Business, $299 for Team, $1,500 for Security; Enterprise is contracted and does
not go through Paddle. Everything metered is billed **in arrears**, for the
last closed month, as a one-time charge on that subscription
(`POST /subscriptions/{id}/charge`, `effective_from: immediately`) — Paddle
Billing has no metered billing of its own that bills overage.

```
on a Paddle webhook (Paddle-Signature checked; subscription.* , transaction.completed):
   remember org -> subscription; on a paid period, issue a license (step 3)
   and deliver it: hosted -> activate it on the tenant; first license of a
   self-hosted customer -> mail it to them (renewal needs one in force)

on a renewal request (the contract above):
1. Paddle: is this organization's subscription active (or past_due inside
   its grace)?          no -> 204
2. usage present for a closed month not charged yet?
      no usage on a card-paid rung -> 204: paying by card means usage
      reporting is on (AXOR_USAGE_REPORTING); air-gapped is Enterprise
3. sign:  axor-license issue --key-file <vendor.key> \
              --org "<organization>" \
              --workspace-tier <tier from the subscription's price> \
              --expires-at <current_billing_period.ends_at>
      (the ceiling defaults to the rung's allowance —
       community 1, coding 1, business 1, team 10, security 50)
4. charge the closed month once: `billable_nodes` at the rung's overage rate
      coding / business / team $75/node/mo, security $50/node/mo
   and developers past the rung's included ones
      coding $25/developer/mo; business, team, security $40/developer/mo
      (included: community 1, every paid rung 0 — ee/license.py
       TIER_DEVELOPER_SEATS)
   — the statement (`GET /v1/license/invoice?month=…`) carries both lines.
5. return {"license_json": ...}   (204 when nothing newer)
```

**Set `expires_at` to the end of the paid period, not a year out.** The check
is offline: a cancelled subscription cannot be revoked, so a license outlives
the money by exactly however long it was written for. Monthly subscription ⇒
monthly license.

## What the Control Plane does for it

Pricing opens Paddle checkout (`frontend/src/paddle.ts`) with
`customData: {"org": <licensed_to>, "hosted": <hosted>}`, both from
`/v1/license/status` (`hosted` is the backend's `AXOR_HOSTED`) —
`licensed_to` from `/v1/license/status` is the name a license must carry to
activate on that deployment: the tenant on hosted, `AXOR_ORG` self-hosted
(without it there is nothing to issue to, and Pricing says so).

A license the plane installs on its own — a renewal, or one an operator
installs for a tenant (how `axor-billing` delivers a hosted workspace's first)
— is handed to identity's `POST /v1/orgs/{id}/license/renewal`
(`AXOR_IDENTITY_URL`), which takes it with no caller only if it is
vendor-signed, issued to that workspace, in date and ends later than the plan
in force. So single sign-on follows the renewed plan.

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
