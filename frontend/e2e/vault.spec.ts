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
});
