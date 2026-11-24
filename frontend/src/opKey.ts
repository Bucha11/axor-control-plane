// This browser's operator signing key: Ed25519 made by WebCrypto with the
// private half NON-EXTRACTABLE, kept in IndexedDB. The plane gets only the
// public half (bound to the account), so a compromised plane cannot sign —
// it can only ask this browser to, through the page it serves. Lose the
// browser (or clear its data) and the key is gone: make a new one, have an
// operator vouch for it, revoke the old one.
import { canonicalize } from "./jcs";

const DB = "axor-operator";
const STORE = "keys";
const SLOT = "this-browser";

interface Stored { pair: CryptoKeyPair; pubHex: string; operatorId: string | null }

let cached: Stored | null | undefined;

function db(): Promise<IDBDatabase> {
  return new Promise((resolve, reject) => {
    const req = indexedDB.open(DB, 1);
    req.onupgradeneeded = () => req.result.createObjectStore(STORE);
    req.onsuccess = () => resolve(req.result);
    req.onerror = () => reject(req.error);
  });
}

async function tx<T>(mode: IDBTransactionMode, run: (s: IDBObjectStore) => IDBRequest<T>): Promise<T> {
  const d = await db();
  return new Promise((resolve, reject) => {
    const r = run(d.transaction(STORE, mode).objectStore(STORE));
    r.onsuccess = () => resolve(r.result);
    r.onerror = () => reject(r.error);
  });
}

const hex = (buf: ArrayBuffer) => [...new Uint8Array(buf)].map((b) => b.toString(16).padStart(2, "0")).join("");

/** Whether this browser makes Ed25519 keys (all current ones do). */
export async function supported(): Promise<boolean> {
  try {
    await crypto.subtle.generateKey({ name: "Ed25519" }, false, ["sign", "verify"]);
    return typeof indexedDB !== "undefined";
  } catch { return false; }
}

export async function load(): Promise<Stored | null> {
  if (cached !== undefined) return cached;
  try {
    cached = (await tx<Stored | undefined>("readonly", (s) => s.get(SLOT))) ?? null;
  } catch { cached = null; }
  return cached;
}

/** Whether a registered key is ready to sign — sync, after load(). */
export function ready(): boolean {
  return !!cached && !!cached.operatorId;
}

export async function create(): Promise<Stored> {
  const pair = await crypto.subtle.generateKey({ name: "Ed25519" }, false, ["sign", "verify"]) as CryptoKeyPair;
  const pubHex = hex(await crypto.subtle.exportKey("raw", pair.publicKey));
  const stored: Stored = { pair, pubHex, operatorId: null };
  await tx("readwrite", (s) => s.put(stored, SLOT));
  cached = stored;
  return stored;
}

export async function setOperatorId(operatorId: string): Promise<void> {
  const k = await load();
  if (!k) return;
  const next = { ...k, operatorId };
  await tx("readwrite", (s) => s.put(next, SLOT));
  cached = next;
}

export async function forget(): Promise<void> {
  await tx("readwrite", (s) => s.delete(SLOT));
  cached = null;
}

/** ``{operator, sig}`` over the JCS bytes of ``payload``; null with no key. */
export async function sign(payload: unknown): Promise<{ operator: string; sig: string } | null> {
  const k = await load();
  if (!k || !k.operatorId) return null;
  const bytes = new TextEncoder().encode(canonicalize(payload));
  const sig = await crypto.subtle.sign({ name: "Ed25519" }, k.pair.privateKey, bytes);
  return { operator: k.operatorId, sig: hex(sig) };
}
