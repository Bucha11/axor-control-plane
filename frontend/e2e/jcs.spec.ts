// JCS byte-parity in CI: the browser canonicalizer (src/jcs.ts) must reproduce
// every test vector's canonical form exactly, so the signature the operator's
// browser produces is the one the adapter verifies (protocol §6). Pure test —
// no page, no servers — Playwright transpiles the TS import for us.
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";
import { expect, test } from "@playwright/test";
import { canonicalize } from "../src/jcs";

const here = dirname(fileURLToPath(import.meta.url));
const doc = JSON.parse(
  readFileSync(resolve(here, "../../test-vectors/jcs-signing.json"), "utf8"),
) as { vectors: { name: string; payload: unknown; canonical: string }[] };

test.describe("JCS canonicalizer (RFC 8785, float-free subset)", () => {
  for (const v of doc.vectors) {
    test(`byte-parity: ${v.name}`, () => {
      expect(canonicalize(v.payload)).toBe(v.canonical);
    });
  }

  test("rejects floats, NaN/Infinity, and undefined", () => {
    expect(() => canonicalize({ x: 1.5 })).toThrow();
    expect(() => canonicalize({ x: NaN })).toThrow();
    expect(() => canonicalize({ x: Infinity })).toThrow();
    expect(() => canonicalize({ x: undefined })).toThrow();
    expect(() => canonicalize(() => 0)).toThrow();
  });
});
