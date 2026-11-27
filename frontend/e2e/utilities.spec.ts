// There is no Tailwind here: utilities.css is a hand-written subset, and a
// class it does not define does nothing — silently. `mb-4` sat on ~20 call
// sites that way. This walks every className string literal in
// src/ and fails on a spacing/layout-looking name the stylesheet lacks.
// No browser: it reads files.
import { readFileSync, readdirSync, statSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { expect, test } from "@playwright/test";

const SRC = join(dirname(fileURLToPath(import.meta.url)), "..", "src");

const UTILITY =
  /^(-?[mp][trblxy]?-|gap(-[xy])?-|space-[xy]-|flex|inline-|block$|hidden$|grid|col-|row-|items-|justify-|self-|content-|place-|order-|grow|shrink|basis-|[wh]-|min-[wh]-|max-[wh]-|overflow|absolute$|relative$|fixed$|sticky$)/;

function walk(dir: string): string[] {
  return readdirSync(dir).flatMap((f) => {
    const p = join(dir, f);
    if (statSync(p).isDirectory()) return walk(p);
    return /\.(tsx?|jsx?)$/.test(f) ? [p] : [];
  });
}

test("every utility class used in a className is defined in utilities.css", () => {
  const css = readFileSync(join(SRC, "utilities.css"), "utf8");
  const defined = new Set(
    [...css.matchAll(/\.((?:[\w-]|\\.)+)/g)].map((m) => m[1].replace(/\\/g, "")),
  );
  const missing: string[] = [];
  for (const file of walk(SRC)) {
    const text = readFileSync(file, "utf8");
    for (const m of text.matchAll(/className=(?:"([^"]*)"|\{([^}]*)\})/g)) {
      const strings = m[1] !== undefined
        ? [m[1]]
        : [...m[2].matchAll(/"([^"]*)"|'([^']*)'|`([^`]*)`/g)].map((s) => s[1] ?? s[2] ?? s[3]);
      for (const cls of strings.join(" ").split(/\s+/)) {
        if (cls && !cls.includes("$") && UTILITY.test(cls) && !defined.has(cls)) {
          const line = text.slice(0, m.index).split("\n").length;
          missing.push(`${cls}  (${file.slice(SRC.length + 1)}:${line})`);
        }
      }
    }
  }
  expect(missing, "add these to utilities.css, or drop them at the call site").toEqual([]);
});
