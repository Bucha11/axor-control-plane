# The compliance pack, and the legal hold

A reviewer asking *"show me that your AI agents were governed last quarter"*
wants one document, not a tour of screens. Both of these are on the
**Enterprise** plan, in **Settings → audit**.

## The pack

Choose a period — it opens on the quarter just ended — and either **read it**
(a document in a new tab) or **save the JSON** (the same content, for an
archive or a GRC tool). The two are the same pack; the JSON is what a machine
reads, the document is what a person reads.

What is in it, each section naming where its rows came from:

| Section | From |
|---|---|
| What was governed | the agents that reported to this plane, and the developers running coding agents |
| Human oversight | every pause, stop, cap, excision, injection, replan and team brake — who, when, on whose agent, with the reason given |
| Attestations | operator attestations on the plane: a reason is required and they are append-only |
| Policy changes | rules, exceptions, trusted sources, skills, team memory |
| Access | members, roles, sign-in (SSO, SCIM), permissions, session limits |
| Credentials | every key minted and revoked |
| Record-keeping | how many acts, over what span, the retention in force, and the legal holds |
| What this answers | each section against the obligation it evidences |

**It only claims what it can show.** A section with nothing in it says so
rather than disappearing: "nobody intervened" and "we have no record of
interventions" are different answers, and the difference is the point of the
document. The record-keeping section carries a coverage line in plain words —
if the trail starts later than the period asked for, it says so and why.

**Counts are never summed across sections.** "61 agents" and "5 interventions"
do not add up to anything a reviewer could use, so each line of *What this
answers* carries its numbers in their own units.

### Checking the copy you were handed

Every pack carries a sha256 over its own body with the `integrity` field
removed, keys sorted, compact JSON:

```sh
python3 -c "import json,hashlib,sys; d=json.load(open('pack.json')); \
  d.pop('integrity'); \
  print(hashlib.sha256(json.dumps(d,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()).hexdigest())"
```

Two generations of the same period are two documents — the generation time is
part of what is hashed — alike in everything they report.

For a signature, pass `sign=<key id>` for a key held in this deployment's
signing custody (Settings → security). The signature covers the digest, and
the request goes through the vault's own signing token: the pack is not a way
around that wall. The auditor verifies it against the public half you gave
them:

```sh
# signature_hex and signed_by are in the pack's integrity block
python3 -c "from nacl.signing import VerifyKey; import json,sys; \
  d=json.load(open('pack.json')); \
  VerifyKey(bytes.fromhex('<public key hex>')).verify( \
    d['integrity']['sha256'].encode(), bytes.fromhex(d['integrity']['signature']))" \
  && echo ok
```

## The legal hold

A workspace sets how long it keeps its audit trail (Business). **While a hold
is on, nothing is pruned** — whatever retention says. A retention policy that
quietly deletes evidence during a dispute is worse than no policy.

- Put one on with a reason (a case, an investigation). It is on the trail.
- Lift it when the matter closes. That is on the trail too, and the hold is
  kept, marked released: that it existed, and for how long, is part of the
  record. Pruning resumes at the next sweep.
- Every pack carries the holds, so a frozen period says so on its face.

## What this is not

This is a read of what the plane already recorded. It collects nothing new, it
makes no judgement about whether your agents were used well, and the mapping
to obligations is this product's reading of them, not legal advice —
`docs/eu-ai-act-mapping.md` explains each line and what is out of scope.
