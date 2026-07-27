// JCS — JSON Canonicalization Scheme (RFC 8785), the browser twin of the pure
// kernel canonicalizer (axor_core.kernel.jcs). The operator's browser produces
// the exact canonical bytes it signs, so a compromised backend relay cannot
// forge a command: the signer, not the relay, decides what was signed
// (protocol §6). Both sides MUST agree byte-for-byte — the test vectors in
// test-vectors/jcs-signing.json are the contract, checked by verify-jcs.mjs and
// the e2e jcs.spec.ts.
//
// Scope (matching the kernel): signed payloads carry only objects, arrays,
// strings, integers, booleans and null. Floats are rejected — an IEEE-754
// amount has no place in a signed command, and refusing them sidesteps RFC 8785
// §3.2.2.3's number-formatting subtleties. No dependencies.

// Mandatory two-char escapes (RFC 8785 §3.2.2.2 / RFC 8259).
const ESCAPES: Record<string, string> = {
  "\\": "\\\\",
  '"': '\\"',
  "\b": "\\b",
  "\f": "\\f",
  "\n": "\\n",
  "\r": "\\r",
  "\t": "\\t",
};

function escapeString(s: string): string {
  let out = '"';
  for (const ch of s) {
    const esc = ESCAPES[ch];
    if (esc !== undefined) {
      out += esc;
    } else if (ch < "\x20") {
      // Remaining C0 controls -> \u00xx (lowercase hex, matching the kernel).
      out += "\\u" + ch.charCodeAt(0).toString(16).padStart(4, "0");
    } else {
      out += ch; // literal — becomes UTF-8 when the string is byte-encoded
    }
  }
  return out + '"';
}

function serialize(value: unknown): string {
  if (value === null) return "null";
  if (value === true) return "true";
  if (value === false) return "false";

  const t = typeof value;

  if (t === "string") return escapeString(value as string);

  if (t === "number") {
    const n = value as number;
    if (!Number.isFinite(n)) {
      throw new Error("JCS: NaN/Infinity are not permitted in signed payloads");
    }
    if (!Number.isInteger(n)) {
      throw new Error("JCS: floats are not permitted in signed payloads");
    }
    // Shortest integer form (no plus, no leading zeros). Reject magnitudes that
    // lose integer precision or would stringify in exponent form — neither is a
    // canonical integer, and neither belongs in a governance payload.
    if (!Number.isSafeInteger(n)) {
      throw new Error("JCS: integer out of safe range");
    }
    const s = String(n === 0 ? 0 : n); // normalise -0 -> "0"
    if (!/^-?\d+$/.test(s)) {
      throw new Error(`JCS: non-canonical integer ${s}`);
    }
    return s;
  }

  if (t === "bigint") {
    // Integers beyond the safe range are expressible as bigint and canonicalize
    // exactly (shortest decimal form).
    return (value as bigint).toString();
  }

  if (Array.isArray(value)) {
    return "[" + value.map((v) => serialize(v)).join(",") + "]";
  }

  if (t === "object") {
    const obj = value as Record<string, unknown>;
    // Reject anything not a plain string-keyed object (Map, Set, typed arrays…).
    const proto = Object.getPrototypeOf(obj);
    if (proto !== Object.prototype && proto !== null) {
      throw new Error(`JCS: unserializable object ${obj.constructor?.name ?? "?"}`);
    }
    // Object.keys yields only string keys; sort by UTF-16 code units — the
    // default JS string order, which is exactly RFC 8785 §3.2.3 (and the
    // kernel's _utf16_key). Symbol keys are ignored by Object.keys, matching
    // "keys must be strings".
    const keys = Object.keys(obj).sort();
    const items = keys.map((k) => escapeString(k) + ":" + serialize(obj[k]));
    return "{" + items.join(",") + "}";
  }

  // undefined, function, symbol.
  throw new Error(`JCS: unserializable value of type ${t}`);
}

/**
 * RFC 8785 canonical form (float-free subset) of a JSON value, as a string.
 * Encode with TextEncoder to obtain the exact bytes an operator signs.
 */
export function canonicalize(value: unknown): string {
  return serialize(value);
}
