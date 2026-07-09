# Launch owner checklist — the credential/VPS items only

Everything code-side in docs/launch-readiness-v0.1.md is done. What remains
needs the OWNER's accounts, money, voice, or a machine outside this repo.
Ordered so each step unblocks the next; ~2–3 working days end to end.

## 1. Unblock CI: ecosystem repo access (15 min)

CI's deploy job and the release image builds fetch `axor-core`/`axor-eval`
from GitHub; the fallback `GITHUB_TOKEN` cannot read other private repos.

**Path A (recommended — PyPI needs the code public anyway):** make
`bucha11/axor-core` and `bucha11/axor-eval` public. Each repo → Settings →
General → Danger Zone → Change visibility. Both are Apache-2.0 already.

**Path B:** GitHub → Settings → Developer settings → Fine-grained tokens →
Generate: resource owner `bucha11`, repos `axor-core` + `axor-eval`,
permissions Contents: Read-only. Then in `axor-control-plane` → Settings →
Secrets and variables → Actions → new secret `AXOR_ECOSYSTEM_TOKEN`.

**Verify:** re-run CI on main; the `deploy` job (compose build + smoke) goes
green. That flips the last ◐ in launch-readiness §6.

## 2. PyPI release (60–90 min, order matters)

Platform wheels depend on `axor-core>=0.1` and `axor-eval>=0.1` — publish
those FIRST or installs won't resolve. Current versions: axor-core 0.8.0
(dependency-free), axor-eval 0.1.0 (needs axor-core>=0.8,<0.9).

1. Create a pypi.org account, enable 2FA.
2. First-time manual publish of the ecosystem packages (they have no release
   workflow yet): in each repo, `uv build && uvx twine upload dist/*` with a
   PyPI API token (Account settings → API tokens). Order: axor-core, then
   axor-eval. Check the names are free on pypi.org first.
3. Trusted publishing for the platform (no token ever stored): pypi.org →
   Your account → Publishing → add a **pending publisher** twice — project
   `axor-proxy` and `axor-backend`, owner `Bucha11`, repository
   `axor-control-plane`, workflow `release.yml`, environment `release`.
4. GitHub: `axor-control-plane` → Settings → Environments → create
   `release` (release.yml pins it).
5. Tag: `git tag v0.1.0 && git push origin v0.1.0` — the workflow publishes
   both packages to PyPI and both images to GHCR.
6. **Verify on a clean machine:** `uvx axor-proxy --demo` runs; the GHCR
   images pull. Every `uvx` mention in the UI/docs stops 404ing.

## 3. GitHub Pages + Discussions (10 min)

- Pages: `axor-control-plane` → Settings → Pages → Source = **GitHub
  Actions**. `pages.yml` deploys `site/` on the next push to main (or run it
  via workflow_dispatch). Custom domain: Settings → Pages → add it, create a
  DNS CNAME to `bucha11.github.io`, tick Enforce HTTPS.
- Discussions: Settings → General → Features → enable. Seed two categories:
  Q&A and Show-and-tell (caught-lie receipts). Flips the ◐ in §3.

## 4. Domain + mailboxes (30 min)

The repo already points at `axor.dev`: `sales@` (site CTAs, Pricing mailto)
and `security@` (SECURITY.md). Either register that domain or grep-replace
the addresses. Cheapest working setup: Cloudflare Registrar + free Email
Routing (`security@` and `sales@` → your inbox). Send yourself a test mail
from outside. Then point GitHub Pages at the domain (step 3).

## 5. Stripe payment link (20 min)

1. Stripe → Product: "Axor Team" (subscription, e.g. $500/mo as the floor;
   quantity = nodes) → Payment Links → create → copy the URL.
2. Compose deploy: put `VITE_CHECKOUT_URL=https://buy.stripe.com/…` in
   `.env`, rebuild: `docker compose build frontend && docker compose up -d`.
   The build arg is already plumbed; empty keeps the mailto fallback.
3. Verify: Pricing → "Get Team" opens Stripe checkout. A stranger can pay —
   launch outcome #2 done.

## 6. VPS dress rehearsal (half a day — the highest-value item)

Rent a clean Ubuntu 24.04 box (Hetzner CX22-class, 2 vCPU/4 GB is enough).
Follow ONLY the README, as a stranger would:

```
ssh root@<vps>
curl -fsSL https://get.docker.com | sh
git clone https://github.com/Bucha11/axor-control-plane && cd axor-control-plane
cp .env.example .env   # set AXOR_PG_PASSWORD + AXOR_API_TOKEN (openssl rand -hex 24)
docker compose up -d
```

Walk the three launch outcomes with a stopwatch:
1. Open :8080 → onboarding → demo run → EvidenceCase in **under 10 minutes**.
2. Share an EvidenceCase link; open it from your phone (external network).
3. Settings → subscribe a real Slack webhook; pause a node; the alert lands.

Every snag goes straight into README/quickstart as a fix, then re-run from
scratch. Optionally re-run `scripts/load_smoke.py` against the VPS before
quoting ops-limits numbers for that hardware class.

## 7. The 5-minute video (2–3 h)

Shot list is written: docs/video-script.md. Record at 1080p (OBS or Screen
Studio) against the VPS deploy from step 6, your voice. Upload unlisted to
YouTube → link from README hero + site. Keep take 1 — authentic beats polished.

## 8. Per-model benchmark rows (needs LLM keys, ~1 h + API spend)

BENCHMARKS.md ships the deterministic persona table (100% recall / 0% FP).
For the paper/launch post, add real-model rows: export `ANTHROPIC_API_KEY`
(and friends), swap the persona loop for an LLM call as described in
axor-eval `benchmarks/`, run `--trials 20 --write` per model. Update the
launch-post table.

## 9. Design partners (paperwork is drafted — execution only)

Everything is in `docs/partner/`: external one-pager, outreach templates +
qualification checklist, Design Partner Agreement, mutual NDA, and a DPA
template (dormant while self-hosted — Axor processes no partner data). Your
steps:

1. Fill the `[BRACKETS]` (legal name, jurisdiction) in the agreement + NDA.
2. Add the video/site links to the one-pager and outreach email (after
   steps 6–7).
3. Source 15–20 qualified conversations from the three pipelines in
   `docs/design-partner-kit.md`; run the qualification checklist; sign via
   any free e-sign tier.
4. Rule from `docs/partner/README.md`: the first PAID deal or any HOSTED
   data is the trigger for professional legal review — not before.

## 10. Paper venue (15 min decision)

Pick the nearest agent-safety/reliability workshop deadline first (fast
review, citable), main venue after. The artifact ships from the ecosystem
packages (Apache-2.0) per monetization §7 — already publishable once step 2
makes them public.

---

Suggested order: 1 → 2 → 3 → 4 → 5 (one sitting), then 6 → 7 (the
rehearsal + footage), 8–10 in parallel while feedback accumulates. After
step 6 passes from-scratch, you are launch-ready by the three-outcome rule.
