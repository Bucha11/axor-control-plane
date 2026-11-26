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
    await expect(page.getByRole("heading", { name: "What each of your agent's tools may do" })).toBeVisible();

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

    await expect(page.getByTestId("policy-tool-read_inbox")).toBeVisible();
    await expect(page.getByTestId("policy-tool-send_email")).toBeVisible();

    // The scanner's guesses arrive in words, each one a choice to correct;
    // what it returns being outside text is a mark of its own, as on Connect.
    await expect(page.getByLabel("what send_email does")).toHaveValue("EXPORT");
    await expect(page.getByTestId("policy-tool-send_email")).toContainText("guessed from 'send' in the name");
    const inbox = page.getByLabel("read_inbox brings in outside text");
    if (!(await inbox.isChecked())) await inbox.check();
    // what the mark does is said where it is set: the legend, and per tool
    await expect(page.getByTestId("outside-legend")).toContainText("that call is refused");
    await expect(page.getByTestId("outside-effect-read_inbox")).toContainText("what it returns can't drive send_email");
    for (const pick of await page.getByLabel(/^what .* does$/).all()) {
      if ((await pick.inputValue()) === "?") await pick.selectOption("READ");
    }
    await page.getByRole("button", { name: /See what gets refused/ }).click();
    await expect(page.getByRole("heading", { name: "What gets let through, and what is refused" })).toBeVisible();
    const reading = page.getByTestId("policy-reading");
    await expect(reading).toContainText("read_inbox — allowed. What it returns is marked as outside text.");
    await expect(reading).toContainText("send_email — refused if its “to” came from outside text");
    await expect(reading).toContainText("Any other tool — refused.");
    // the files are there for whoever wants them, under the names Connect loads
    await page.getByText("or download the files").click();
    const [file] = await Promise.all([
      page.waitForEvent("download"),
      page.getByRole("button", { name: /axor\.manifests\.json/ }).click(),
    ]);
    expect(file.suggestedFilename()).toBe("axor.manifests.json");
    // and the next step starts from the same tools
    await page.getByTestId("policy-to-wrap").click();
    await expect(page).toHaveURL(/#\/connect\?for=production&how=code$/);
    await expect(page.getByLabel("what send_email does")).toHaveValue("EXPORT");
  });

  test("offers a hand-declared path too", async ({ page }) => {
    await goHash(page, "config-builder");
    await expect(page.getByRole("button", { name: /add the tools by hand instead/ })).toBeVisible();
  });

  test("max tool calls: an invalid value is said so, never taken as no limit (QA-023)", async ({ page }) => {
    await goHash(page, "config-builder");
    await page.getByRole("button", { name: /add the tools by hand instead/ }).click();
    await page.getByRole("button", { name: "Add tool" }).click();
    await page.getByPlaceholder("tool name ⏎").fill("read_inbox");
    await page.getByPlaceholder("tool name ⏎").press("Enter");
    await page.getByRole("button", { name: /limit tool calls/ }).click();
    const cap = page.getByPlaceholder("unlimited");
    const see = page.getByRole("button", { name: /See what gets refused/ });
    await cap.fill("25");
    await expect(page.getByText("past this, every call in the run is refused")).toBeVisible();
    for (const bad of ["0", "-5", "3.7", "999999999999999999999"]) {
      await cap.fill(bad);
      await expect(cap).toHaveValue(bad);   // the field keeps what was typed
      await expect(page.getByText("a whole number from 1 to 1,000,000 — or empty for no limit")).toBeVisible();
      await expect(see).toBeDisabled();
    }
    await cap.fill("");
    await expect(page.getByText("empty = no limit")).toBeVisible();
    await expect(see).toBeEnabled();
  });

  test("per tool: which arguments decide, and how bad if it goes wrong", async ({ page }) => {
    await goHash(page, "config-builder");
    await page.getByTestId("wrap-files").setInputFiles([
      { name: "tools.py", mimeType: "text/x-python", buffer: Buffer.from(AGENT_PY) },
    ]);
    const notInstalled = page.getByText("wrap engine not installed on the backend");
    const row = page.getByTestId("policy-tool-send_email");
    await expect(row.or(notInstalled)).toBeVisible({ timeout: 15_000 });
    if (await notInstalled.isVisible()) return;
    await row.getByRole("button", { name: /send_email/ }).click();
    const decides = page.getByTestId("decides-send_email");
    // the scanner's guess: `to` decides, `body` does not
    await expect(decides.getByTestId("decides-send_email-to")).toContainText("✓");
    await expect(decides).toContainText("in body it passes");
    await decides.getByLabel("allowed to").fill("boss@corp.example");
    await decides.getByLabel("allowed to").press("Enter");
    await expect(decides).toContainText("any other value is refused");
    await page.getByTestId("consequence-send_email").getByRole("button", { name: "can't be undone" }).click();
    await expect(page.getByTestId("consequence-send_email")).toContainText("refused — unless the agent's own approval hook grants that one call");
    await page.getByRole("button", { name: /See what gets refused/ }).click();
    const reading = page.getByTestId("policy-reading");
    await expect(reading).toContainText("Only to boss@corp.example — any other value is refused.");
    await expect(reading).toContainText("there is no approval from this plane");
  });

  test("the config beside the form: a click shows where, a hand edit comes back", async ({ page }) => {
    await goHash(page, "config-builder");
    await page.getByTestId("wrap-files").setInputFiles([
      { name: "tools.py", mimeType: "text/x-python", buffer: Buffer.from(AGENT_PY) },
    ]);
    const notInstalled = page.getByText("wrap engine not installed on the backend");
    const row = page.getByTestId("policy-tool-send_email");
    await expect(row.or(notInstalled)).toBeVisible({ timeout: 15_000 });
    if (await notInstalled.isVisible()) return;
    await page.getByTestId("config-split").click();
    const text = page.getByLabel("the config, as JSON");
    await expect(text).toHaveValue(/"send_email": \{/);
    // form → text
    await row.getByRole("button", { name: /send_email/ }).click();
    await page.getByTestId("consequence-send_email").getByRole("button", { name: "can't be undone" }).click();
    await expect(text).toHaveValue(/"consequence": "CATASTROPHIC"/);
    // text → form
    const edited = (await text.inputValue()).replace('"consequence": "CATASTROPHIC"', '"consequence": "REVERSIBLE"');
    await text.fill(edited);
    await expect(page.getByTestId("consequence-send_email")).toContainText("let through", { timeout: 5_000 });
    // a wrong value: the plane says which field, the form keeps what it had
    await text.fill(edited.replace('"consequence": "REVERSIBLE"', '"consequence": "very bad"'));
    await expect(page.getByTestId("config-error")).toContainText("consequence");
    await expect(page.getByTestId("consequence-send_email")).toContainText("let through");
    // broken JSON: the line is named
    await text.fill('{\n  "sinks": {,\n}');
    await expect(page.getByTestId("config-error")).toContainText("line 2");
  });
});
