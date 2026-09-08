// M6: Federation Vault (spec v2 Ch.5) — two panes, visibly separate. Tool
// credentials are dispensed and scoped; signing keys are signed-not-
// surrendered with an audit trail. The e2e backend runs the open dev posture
// (no vault tokens), so seeding goes straight through the API.
import { expect, request, test } from "@playwright/test";
import { BACKEND, goHash, setConnection } from "./helpers";

test.describe("federation vault", () => {
  test("settings shows the two walled panes with real state", async ({ page }) => {
    const ctx = await request.newContext();
    await ctx.post(`${BACKEND}/v1/vault/creds/enroll`, {
      data: { tool: "payments", endpoint: "https://pay.example",
              secret: "sk_1", scope_nodes: ["billing-agent"] },
    });
    await ctx.post(`${BACKEND}/v1/vault/signing/keys`, {
      data: { key_id: "fed-main", operators: ["op_dmitrii"] },
    });
    await ctx.post(`${BACKEND}/v1/vault/signing/sign`, {
      data: { key_id: "fed-main", operator: "op_dmitrii",
              payload_b64: Buffer.from("cmd").toString("base64") },
    });
    await ctx.dispose();

    await setConnection(page, { mode: "adapter" });
    await goHash(page, "settings");

    const vault = page.getByTestId("federation-vault");
    await expect(vault).toBeVisible();
    // pane 1: tool credentials — enrolled, scoped
    await expect(vault.getByText("FEDERATION VAULT · TOOL CREDENTIALS")).toBeVisible();
    await expect(vault.getByText("payments")).toBeVisible();
    await expect(vault.getByText(/scope: billing-agent/)).toBeVisible();
    // pane 2: signing — key with pubkey prefix + audited sign request
    await expect(vault.getByText("FEDERATION VAULT · SIGNING KEYS")).toBeVisible();
    await expect(vault.getByText("fed-main").first()).toBeVisible();
    await expect(vault.getByText("SIGN-REQUEST AUDIT")).toBeVisible();
    await expect(vault.getByText("signed", { exact: true }).first()).toBeVisible();
    // the private half never renders anywhere
    await expect(vault.getByText(/seed/)).not.toBeVisible();
  });
  // NOTE ON ORDER: registering a sealing key is a one-way switch for the run —
  // after it, this deployment refuses plaintext enrolment. So the plaintext
  // cases come first, deliberately, and the envelope ones follow.

  test("enrolling without a sealing key says the secret is stored as-is", async ({ page }) => {
    await setConnection(page, { mode: "adapter" });
    await goHash(page, "settings");
    await expect(
      page.getByText(/this deployment stores credentials in PLAINTEXT/),
    ).toBeVisible();

    await page.getByRole("button", { name: /enrol a credential/ }).click();
    await expect(page.getByText(/sent as plaintext/)).toBeVisible();
    await page.getByLabel("tool").fill("search");
    await page.getByLabel("endpoint").fill("https://s.example");
    await page.getByLabel("scope_nodes (comma-separated)").fill("n1");
    await page.getByLabel("secret").fill("sk_plain_1");

    const sent = page.waitForRequest((r) => r.url().includes("/creds/enroll"));
    await page.getByRole("button", { name: "enrol", exact: true }).click();
    const body = JSON.parse((await sent).postData() ?? "{}");
    expect(body.secret).toBe("sk_plain_1");
    expect(body.sealed_secret).toBeUndefined();
    await expect(page.getByText("search")).toBeVisible();
  });

  test("revoking through the UI narrows the credential", async ({ page }) => {
    await setConnection(page, { mode: "adapter" });
    await goHash(page, "settings");
    await page.getByRole("button", { name: "revoke search" }).click();
    await expect(page.getByText("revoked")).toBeVisible();
  });

  test("registering a sealing key turns the deployment's own reading off", async ({ page }) => {
    // The PUBLIC half only. `axor-proxy vault keygen` mints the pair on the
    // machine that will hold the private one; it is never typed here.
    await setConnection(page, { mode: "adapter" });
    await goHash(page, "settings");
    await page.getByLabel("sealing pubkey (envelope mode)")
      .fill("2f".repeat(32));
    await page.getByRole("button", { name: "register" }).first().click();
    await expect(
      page.getByText(/this deployment stores only what it cannot open/),
    ).toBeVisible();
  });

  test("in envelope mode the plaintext never leaves the browser", async ({ page }) => {
    await setConnection(page, { mode: "adapter" });
    await goHash(page, "settings");
    await page.getByRole("button", { name: /enrol a credential/ }).click();
    await expect(page.getByText(/sealed in this browser/)).toBeVisible();
    await page.getByLabel("tool").fill("stripe");
    await page.getByLabel("endpoint").fill("https://api.stripe.example");
    await page.getByLabel("scope_nodes (comma-separated)").fill("billing-agent");
    await page.getByLabel("secret").fill("sk_live_NEVER_SENT");

    const sent = page.waitForRequest((r) => r.url().includes("/creds/enroll"));
    await page.getByRole("button", { name: "enrol", exact: true }).click();
    const posted = (await sent).postData() ?? "";
    // The whole property, asserted on the wire: ciphertext went, plaintext did not.
    expect(posted).not.toContain("sk_live_NEVER_SENT");
    expect(JSON.parse(posted).sealed_secret).toBeTruthy();
    expect(JSON.parse(posted).secret).toBeUndefined();
    await expect(page.getByText("stripe")).toBeVisible();
    await expect(page.getByText("sealed").first()).toBeVisible();
  });
});
