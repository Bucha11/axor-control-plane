# Data Processing Agreement (DPA) — template

*Plain-language GDPR Art. 28-style template. Not legal advice; see
README.md. **Read "When this applies" before sending it to anyone.***

## When this applies (usually: it doesn't)

Axor Control Plane v0.x is **self-hosted**: the proxy, backend, traces, and
database run entirely on the customer's infrastructure. Axor receives no
traffic, no traces, no personal data, and the Software has no phone-home
telemetry (license verification is offline). In that deployment **Axor is
not a data processor** and no DPA is needed — the customer is both
controller and (self-)processor on their own systems.

Send this DPA only when one of these becomes true:

1. Axor operates a **hosted** offering that stores or transits customer data.
2. Axor receives customer traces/exports for support or debugging (even
   temporarily) — scope it to exactly that data.
3. A partner's procurement insists on a signed DPA despite self-hosting —
   then Annex A is filled with "no processing; self-hosted deployment" and
   the DPA is effectively dormant. Signing a dormant DPA is fine.

If (1) ever happens, get this professionally reviewed first — hosted data is
the line where template-only stops being acceptable.

---

This Data Processing Agreement ("DPA") is made on **[DATE]** between
**[CUSTOMER LEGAL NAME]** ("**Controller**") and **[VENDOR LEGAL NAME]**
("**Processor**"), and supplements the [Design Partner Agreement / service
agreement] dated [DATE] (the "**Main Agreement**").

## 1. Subject matter, duration, nature and purpose

Processor processes Personal Data only as needed to provide the services in
the Main Agreement, for its duration, as described in **Annex A** (scope of
processing, data categories, data subjects).

## 2. Controller instructions

Processor processes Personal Data only on documented instructions from
Controller (including the Main Agreement and this DPA), unless required by
law — in which case Processor informs Controller before processing, where
lawful. Processor promptly flags instructions it believes violate data
protection law.

## 3. Confidentiality and personnel

Persons authorised to process Personal Data are bound by confidentiality
(contract or statute) and access only what their role requires.

## 4. Security

Processor implements appropriate technical and organisational measures
("**TOMs**", **Annex B**) considering the state of the art, costs, and the
risks of processing, and reviews them regularly.

## 5. Subprocessors

Controller gives general authorisation for the subprocessors in **Annex C**.
Processor gives at least 30 days' notice before adding or replacing one;
Controller may object on reasonable data-protection grounds, and if no
resolution is found may terminate the affected services. Processor remains
fully liable for its subprocessors.

## 6. Data subject rights and assistance

Taking into account the nature of processing, Processor assists Controller
with appropriate technical and organisational measures in responding to data
subject requests (access, rectification, erasure, portability, objection),
and with Controller's obligations on security, breach notification, impact
assessments and prior consultation. Processor forwards data subject requests
it receives directly to Controller without responding, unless legally
required.

## 7. Personal data breach

Processor notifies Controller **without undue delay and within 72 hours** of
becoming aware of a personal data breach affecting Controller's Personal
Data, with the information reasonably available (nature, categories and
approximate numbers, likely consequences, measures taken), supplemented as
it becomes available.

## 8. Deletion and return

At the end of services, Processor deletes or returns (Controller's choice)
all Personal Data and deletes existing copies, unless law requires storage.
Deletion is confirmed in writing on request.

## 9. Audits

Processor makes available the information necessary to demonstrate
compliance with this DPA and allows audits — starting with documentation and
security reports (see Annex B); on-site audits at most annually, with 30
days' notice, during business hours, under confidentiality, at Controller's
cost unless the audit reveals material non-compliance.

## 10. International transfers

Processor does not transfer Personal Data outside [the EEA/UK/the agreed
region] without a valid transfer mechanism (adequacy decision, Standard
Contractual Clauses, or equivalent). Where SCCs are needed, the parties
incorporate the EU Commission SCCs (Module 2, controller→processor) by
reference, with Annexes A–C serving as their appendices.

## 11. Liability and order of precedence

Liability follows the Main Agreement's limitations, except where data
protection law does not permit them. For processing of Personal Data, this
DPA prevails over conflicting Main Agreement terms.

---

## Annex A — Scope of processing

| Field | Value |
|---|---|
| Deployment model | **[Self-hosted — no processing by Processor / Hosted]** |
| Nature & purpose | [e.g. operating the hosted control plane: ingesting agent traces, serving dashboards] |
| Categories of data | [e.g. agent tool-call metadata: tool names, sizes, hashes, timestamps; operator emails. Raw request/response bodies are NOT stored by design] |
| Special categories | None intended; Controller must not route special-category data through hosted components |
| Data subjects | [Controller's employees (operators); indirectly, individuals appearing in agent workloads] |
| Duration | Term of the Main Agreement + deletion per §8 |

## Annex B — Technical and organisational measures (TOMs)

Baseline (extend for a hosted offering): see `SECURITY.md` and
`docs/security-controls.md` in the product repository — access control with
scoped API keys (hashed at rest), Ed25519-signed operator commands,
no-raw-bodies observation design, TLS in transit, encrypted backups,
retention limits (`AXOR_RETENTION_DAYS`), structured audit logging,
dependency audit + SBOM in CI, vulnerability disclosure process.

## Annex C — Approved subprocessors

| Subprocessor | Purpose | Location |
|---|---|---|
| *(none)* | | |

| | Controller | Processor |
|---|---|---|
| Signature | ______________________ | ______________________ |
| Name | [NAME] | [NAME] |
| Title | [TITLE] | [TITLE] |
| Date | [DATE] | [DATE] |
