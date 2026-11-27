// QA-043: a malformed % escape anywhere in the hash — a link cut short by a mail
// client — threw a URIError while rendering, and the whole app went blank until
// a reload. The link is read as written, and one failing page leaves the rest.
import { expect, test } from "@playwright/test";
import { goHash, setConnection } from "./helpers";

test.beforeEach(async ({ page }) => {
  await setConnection(page, { mode: "demo" });
});

for (const hash of ["pricing?for=%E0%A4%A", "connect?for=%", "home?x=%ZZ", "invite/%E0%A4%A"]) {
  test(`a malformed link does not blank the app: #/${hash} (QA-043)`, async ({ page }) => {
    const errors: string[] = [];
    page.on("pageerror", (e) => errors.push(e.message));
    await goHash(page, hash);
    // the shell is there, and moving on works without a reload
    await expect(page.getByText("AXOR").first()).toBeVisible();
    await goHash(page, "pricing");
    await expect(page.getByTestId("tier-coding")).toBeVisible();
    expect(errors.filter((e) => /URI malformed/.test(e))).toEqual([]);
  });
}
