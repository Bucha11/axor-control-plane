// Byte-parity check: canonicalize(vector.payload) MUST equal vector.canonical
// for every vector in test-vectors/jcs-signing.json — the frontend JCS twin of
// the pure kernel canonicalizer (protocol §6). Run: `node scripts/verify-jcs.mjs`.
//
// jcs.ts is TypeScript; we transpile it to JS with esbuild (re-exported by vite,
// a direct dependency) and import the result as an ES module data: URL.
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";
import { transformWithEsbuild } from "vite";

const here = dirname(fileURLToPath(import.meta.url));
const jcsPath = resolve(here, "../src/jcs.ts");
const vectorsPath = resolve(here, "../../test-vectors/jcs-signing.json");

const { code } = await transformWithEsbuild(readFileSync(jcsPath, "utf8"), jcsPath, {
  loader: "ts",
  format: "esm",
});
const mod = await import(
  "data:text/javascript;base64," + Buffer.from(code).toString("base64")
);
const { canonicalize } = mod;

const doc = JSON.parse(readFileSync(vectorsPath, "utf8"));
let pass = 0;
let fail = 0;
for (const v of doc.vectors) {
  const got = canonicalize(v.payload);
  if (got === v.canonical) {
    console.log(`PASS  ${v.name}`);
    pass++;
  } else {
    console.log(`FAIL  ${v.name}`);
    console.log(`  expected: ${v.canonical}`);
    console.log(`  got:      ${got}`);
    fail++;
  }
}
console.log(`\n${pass}/${pass + fail} vectors PASS` + (fail ? ` — ${fail} FAILED` : ""));
process.exit(fail ? 1 : 0);
