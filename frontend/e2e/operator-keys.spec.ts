// This browser's operator key: made by WebCrypto (Ed25519, private half
// non-extractable, kept in IndexedDB), its signature verifies outside the
// browser over the same JCS bytes the plane and the sidecar check, and the
// Settings panel registers it. The e2e plane is open (no identity service), so
// the registration call itself is stubbed; its checks are the backend suite's.
import { createPublicKey, verify } from "node:crypto";
import { expect, test } from "@playwright/test";
import { goHash, setConnection } from "./helpers";

const SESSION = {
  access_token: "acc-token", refresh_token: "ref-token", token_type: "Bearer", expires_in: 900,
  user: { user_id: "usr_1", email: "ada@acme.io" },
  org: { org_id: "org_1", role: "owner", tier: "community" },
};

function json(status: number, body: unknown) {
  return (route: import("@playwright/test").Route) =>
    route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) });
}

test("a key made in the browser signs bytes the plane can verify, and never leaves", async ({ page }) => {
  await setConnection(page, { mode: "adapter" });
  await goHash(page, "settings");
  const out = await page.evaluate(async () => {
    const k = await import("/src/opKey.ts");
    const made = await k.create();
    await k.setOperatorId("ada@acme.io#test");
    const payload = { node_id: "agent-1", version: 1, body: { paused: true }, timestamp: "t" };
    const signed = await k.sign(payload);
    let extractable = true;
    try { await crypto.subtle.exportKey("pkcs8", made.pair.privateKey); } catch { extractable = false; }
    return { pub: made.pubHex, sig: signed!.sig, operator: signed!.operator, extractable };
  });
  expect(out.extractable).toBe(false);
  expect(out.operator).toBe("ada@acme.io#test");
  // the JCS bytes the backend's signed_payload makes for this command
  const bytes = Buffer.from('{"body":{"paused":true},"node_id":"agent-1","timestamp":"t","version":1}');
  const spki = Buffer.concat([Buffer.from("302a300506032b6570032100", "hex"), Buffer.from(out.pub, "hex")]);
  const key = createPublicKey({ key: spki, format: "der", type: "spki" });
  expect(verify(null, bytes, key, Buffer.from(out.sig, "hex"))).toBe(true);
});

test("an admin makes a signing key in Settings, and it is registered", async ({ page }) => {
  await setConnection(page, { mode: "adapter" });
  await page.route("**/identity/v1/login", json(200, SESSION));
  await page.route("**/identity/v1/me", json(200, {
    user: SESSION.user, active_org: "org_1", role: "owner",
    memberships: [{ org_id: "org_1", role: "owner", name: "Acme", tier: "coding" }],
  }));
  await page.route("**/identity/v1/orgs/org_1/members", json(200, { members: [] }));
  const registered: { pubkey?: string }[] = [];
  let list: unknown[] = [];
  await page.route("**/v1/operators", async (route) => {
    if (route.request().method() === "POST") {
      const body = route.request().postDataJSON() as { pubkey: string };
      registered.push(body);
      list = [{ operator_id: `ada@acme.io#${body.pubkey.slice(0, 8)}`, org: "org_1", email: "ada@acme.io",
                pubkey: body.pubkey, label: "", created_ts: "t", revoked_ts: null, endorsement: null }];
      await route.fulfill({ status: 201, contentType: "application/json",
        body: JSON.stringify({ operator_id: `ada@acme.io#${body.pubkey.slice(0, 8)}` }) });
    } else {
      await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ operators: list }) });
    }
  });
  await goHash(page, "settings");
  await page.getByLabel("email", { exact: true }).fill("ada@acme.io");
  await page.getByLabel("password").fill("correct horse");
  await page.getByRole("button", { name: "log in", exact: true }).click();
  const panel = page.getByTestId("operator-keys");
  await panel.getByTestId("op-key-make").click();
  await expect(panel.getByTestId("op-key-mine")).toContainText("this browser signs");
  expect(registered).toHaveLength(1);
  expect(registered[0].pubkey).toMatch(/^[0-9a-f]{64}$/);
  await expect(panel.getByTestId("op-key")).toContainText("not vouched for");
});
