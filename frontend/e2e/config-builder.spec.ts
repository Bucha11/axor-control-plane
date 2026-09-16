// Config Builder — bring your agent, leave governed (ConfigBuilder.tsx). A real
// code upload (POST /v1/wrap/scan, the axor-wrap engine on the backend)
// discovers the agent's tools; the operator assigns a consequence class to
// each, and the builder emits a replayable config. When the backend lacks the
// wrap extra, the builder shows an honest "engine not installed" panel instead.
import { expect, test } from "@playwright/test";
import { goHash } from "./helpers";

const AGENT_PY = [
  "from langchain_core.tools import tool",
  "",
  "",
  "@tool",
  "def read_inbox(folder: str) -> str:",
  '    """Read the user\'s email inbox."""',
  "    return folder",
  "",
  "",
  "@tool",
  "def send_email(to: str, body: str) -> str:",
  '    """Send an email to a recipient."""',
  "    return to",
  "",
].join("\n");

test.describe("config builder", () => {
  test("the privacy answer points at self-hosting, not a command that does not exist", async ({ page }) => {
    // This panel used to promise `uvx axor wrap ./my_agent` — "same screen,
    // pre-filled, nothing uploaded". No such command existed (the binary is
    // axor-wrap, the verb is scan, and it prints a table with no way back into
    // this screen), so the one place a security-conscious user goes to check
    // whether they can trust the upload sent them down a road with no road.
    //
    // The real answer needs no second code path: the deployment image ships the
    // wrap engine, so a self-hosted stack scans on the operator's own machine.
    await goHash(page, "config-builder");
    const panel = page.getByText(/Code shouldn't leave your machine/);
    await expect(panel).toBeVisible();
    await expect(panel).toContainText("docker compose up");
    await expect(panel).not.toContainText("axor wrap");
  });

  test("two files under one name are refused, not silently merged", async ({ page }) => {
    // The plain picker exposes no directory, so both arrive as "tools.py".
    // They used to overwrite each other in the scan's temp tree and the screen
    // said "Found 1 tools" for an agent with two — the missing one undeclared,
    // and undeclared is denied.
    await goHash(page, "config-builder");
    await page.getByTestId("wrap-files").setInputFiles([
      { name: "tools.py", mimeType: "text/x-python", buffer: Buffer.from(AGENT_PY) },
      { name: "tools.py", mimeType: "text/x-python",
        buffer: Buffer.from("# a different module, same name\n") },
    ]);
    const refused = page.getByText(/share the path 'tools.py'/);
    const notInstalled = page.getByText("wrap engine not installed on the backend");
    await expect(refused.or(notInstalled)).toBeVisible({ timeout: 15_000 });
    if (await notInstalled.isVisible()) return;
    await expect(page.getByRole("heading", { name: /Found \d+ tools/ })).toHaveCount(0);
    // …and the way out is on screen: a folder keeps each file's real path.
    await expect(
      page.getByRole("button", { name: /choose a whole folder/ }),
    ).toBeVisible();
    // webkitdirectory is what makes the second picker a folder picker AND what
    // makes the browser fill in webkitRelativePath, which is the actual fix:
    // without it every path is a bare filename and the collision is unavoidable.
    await expect(page.getByTestId("wrap-folder")).toHaveAttribute("webkitdirectory", "");
  });

  test("dropping files still scans them (the entry API falls back to the list)", async ({ page }) => {
    // The drop handler now reads dataTransfer.items so a dropped FOLDER can be
    // walked — the drop zone has always promised that. A synthetic DataTransfer
    // has no filesystem entries behind it, which is exactly the fallback path
    // this asserts: plain files must keep working.
    await goHash(page, "config-builder");
    await page.evaluate((source) => {
      const dt = new DataTransfer();
      dt.items.add(new File([source], "tools.py", { type: "text/x-python" }));
      document.querySelector('[data-testid="wrap-dropzone"]')!
        .dispatchEvent(new DragEvent("drop", { dataTransfer: dt, bubbles: true }));
    }, AGENT_PY);
    const found = page.getByRole("heading", { name: /Found \d+ tools/ });
    const notInstalled = page.getByText("wrap engine not installed on the backend");
    await expect(found.or(notInstalled)).toBeVisible({ timeout: 15_000 });
  });

  test("a dropped folder is walked, and each file keeps its path", async ({ page }) => {
    // The browser only exposes a dropped directory through webkitGetAsEntry, so
    // `dataTransfer.files` was empty and the zone's own promise ("drop your
    // agent folder") did nothing. The entries are duck-typed, so a fake tree
    // stands in for the OS drag Playwright cannot perform — what is under test
    // is the walk: nested files, and the relative path that keeps two tools.py
    // apart.
    await goHash(page, "config-builder");
    const sent = page.waitForRequest((r) => r.url().includes("/v1/wrap/scan"));
    await page.evaluate((source) => {
      const fileEntry = (name: string, body: string) => ({
        isFile: true, name,
        file: (cb: (f: File) => void) => cb(new File([body], name)),
      });
      // Chromium's readEntries hands back at most 100 entries per call and
      // signals the end with an empty batch, so a reader called once silently
      // truncates a large directory. Two-at-a-time here makes that failure
      // reachable in a fixture of four.
      const dirEntry = (name: string, children: unknown[]) => ({
        isFile: false, name,
        createReader: () => {
          let at = 0;
          return {
            readEntries: (cb: (e: unknown[]) => void) => {
              cb(children.slice(at, at + 2));
              at += 2;
            },
          };
        },
      });
      const tree = dirEntry("agent", [
        fileEntry("tools.py", source),
        dirEntry("__pycache__", [fileEntry("junk.py", "x = 1\n")]),
        fileEntry("README.md", "not python"),
        // Past the first batch: only a reader that loops to the empty batch
        // ever sees it.
        dirEntry("plugins", [fileEntry("tools.py", "# a second module\n")]),
      ]);
      const dt = new DataTransfer();
      dt.items.add(new File(["x"], "placeholder"));
      // On the prototype, not the item: `items[0]` hands back a fresh wrapper
      // each access, so a property defined on one is gone by the next read.
      DataTransferItem.prototype.webkitGetAsEntry = () => tree as never;
      document.querySelector('[data-testid="wrap-dropzone"]')!
        .dispatchEvent(new DragEvent("drop", { dataTransfer: dt, bubbles: true }));
    }, AGENT_PY);

    const body = JSON.parse((await sent).postData() ?? "{}") as {
      files: { path: string }[];
    };
    // Real relative paths (so the two tools.py do not collide), no README,
    // and __pycache__ not walked at all.
    expect(body.files.map((f) => f.path).sort()).toEqual([
      "agent/plugins/tools.py", "agent/tools.py",
    ]);
  });

  test("a file the scanner could not parse is named, not dropped", async ({ page }) => {
    await goHash(page, "config-builder");
    await page.getByTestId("wrap-files").setInputFiles([
      { name: "tools.py", mimeType: "text/x-python", buffer: Buffer.from(AGENT_PY) },
      { name: "broken.py", mimeType: "text/x-python",
        buffer: Buffer.from("def broken(:\n") },
    ]);
    const found = page.getByRole("heading", { name: /Found \d+ tools/ });
    const notInstalled = page.getByText("wrap engine not installed on the backend");
    await expect(found.or(notInstalled)).toBeVisible({ timeout: 15_000 });
    if (await notInstalled.isVisible()) return;
    await expect(
      page.getByText(/1 file could not be parsed and was not scanned/),
    ).toBeVisible();
    await expect(page.getByText(/broken\.py — SyntaxError/)).toBeVisible();
  });

  test("scans uploaded code, classifies tools, and previews the config", async ({ page }) => {
    await goHash(page, "config-builder");
    await expect(page.getByRole("heading", { name: "Bring your agent. Leave governed." })).toBeVisible();

    // Real upload: feed a .py file into the hidden picker → /v1/wrap/scan.
    await page.getByTestId("wrap-files").setInputFiles({
      name: "tools.py", mimeType: "text/x-python", buffer: Buffer.from(AGENT_PY),
    });

    // Either the scan succeeds (wrap engine installed on the backend) or the
    // builder shows the honest 501 panel — both are correct product behavior.
    const found = page.getByRole("heading", { name: /Found \d+ tools/ });
    const notInstalled = page.getByText("wrap engine not installed on the backend");
    await expect(found.or(notInstalled)).toBeVisible({ timeout: 15_000 });
    if (await notInstalled.isVisible()) {
      test.info().annotations.push({
        type: "note",
        description: "backend has no axor-wrap (axor-backend[wrap] extra) — 501 path verified",
      });
      return;
    }

    await expect(page.getByText("read_inbox", { exact: true })).toBeVisible();
    await expect(page.getByText("send_email", { exact: true })).toBeVisible();

    // The scanner pre-classifies confident guesses; anything left "?" gets a
    // class by hand until the preview unlocks (unclassified → 0).
    for (let i = 0; i < 6; i++) {
      const readBtn = page.getByRole("button", { name: "READ", exact: true }).first();
      if (!(await readBtn.isVisible().catch(() => false))) break;
      await readBtn.click();
    }
    await page.getByRole("button", { name: /Preview config/ }).click();
    await expect(page.getByRole("heading", { name: "Here's what this config means." })).toBeVisible();
    // The second real artifact: tool manifests via POST /v1/wrap/manifests.
    await expect(page.getByRole("button", { name: /download tool manifests/ })).toBeVisible();
  });

  test("offers a hand-declared path too", async ({ page }) => {
    await goHash(page, "config-builder");
    await expect(page.getByRole("button", { name: /declare sinks by hand instead/ })).toBeVisible();
  });
});
