/**
 * Sandbox executor — runs a materialized bundle end-to-end under a local sandbox identity.
 *
 * This is the `deploy` stage's runtime, one level up from batch.ts: where batch.ts answers "which
 * labels fire?" for the quality report, execute.ts proves the labeler works as a *service* — it
 * loads a per-channel sandbox identity (a real secp256k1 key + a did:web placeholder), evaluates the
 * replay corpus, and emits a signed label record per fired (post, label) into a local store. Nothing
 * is published: the did:web is not served/resolvable in this tier, and records go to disk only. The
 * @skyware/labeler LabelerServer (and atproto-canonical dag-cbor labels) replace this at provision.
 *
 * Signing uses Node's BUILT-IN crypto (no npm — the registry is firewalled): secp256k1 ECDSA over a
 * canonical-JSON serialization of the record. That is a real signature by a real key, but a
 * sandbox-simplified record shape, NOT byte-identical to a prod atproto label.
 *
 * Usage:  node dist/execute.js <bundleDir>   with corpus posts as JSON on stdin.
 * Reads   <bundleDir>/labeler.spec.json, <channelDir>/identity.json (created on first run).
 * Writes  <bundleDir>/labels.jsonl (one signed record per line).
 * Emits   a summary {status, did, total, records_emitted, per_label, examples, records_path} to stdout.
 *         per_label carries an entry for EVERY rule-bearing label, including zeros.
 */

import { compileLabel, evaluateLabel } from "./index.js";
import type { AccountTraits, LabelerSpec } from "./index.js";

// Minimal ambient declarations so the engine stays dependency-free (no @types/node, no node_modules).
declare function require(id: string): any;
declare const Buffer: any;
declare const console: { error(...args: unknown[]): void };
declare const process: {
  argv: string[];
  stdin: { setEncoding(enc: string): void; on(ev: string, cb: (chunk: string) => void): void };
  stdout: { write(s: string): void };
  exit(code: number): void;
};

const crypto = require("crypto");
const fs = require("fs");
const path = require("path");

interface Identity {
  did: string;
  environment: string;
  created_at: string;
  privateKeyPem: string;   // pkcs8 PEM of the secp256k1 key; local sandbox secret
  publicKeyJwk: unknown;
}

interface LabelRecord {
  ver: number;
  src: string;   // the sandbox labeler DID
  uri: string;   // subject post URI
  val: string;   // label value (identifier)
  cts: string;   // created timestamp, ISO-8601
  neg: boolean;  // negation (retraction); always false here
  sig: string;   // base64url secp256k1 signature over the canonical unsigned record
}

interface ExecInput {
  posts: { uri?: string; text?: string; handle?: string; account?: AccountTraits }[];
}

/** Deterministic canonical JSON (sorted keys, no whitespace) so the signed bytes are reproducible. */
function canonical(obj: unknown): string {
  if (obj === null || typeof obj !== "object") return JSON.stringify(obj);
  if (Array.isArray(obj)) return "[" + obj.map(canonical).join(",") + "]";
  const rec = obj as Record<string, unknown>;
  const keys = Object.keys(rec).sort();
  return "{" + keys.map((k) => JSON.stringify(k) + ":" + canonical(rec[k])).join(",") + "}";
}

/** Per-channel sandbox DID: a stable did:web placeholder derived from the channel id. Not served. */
function sandboxDid(channelId: string): string {
  const hash = crypto.createHash("sha256").update(channelId).digest("hex").slice(0, 12);
  return `did:web:sandbox-cleo-${hash}`;
}

/** Load the channel's sandbox identity, or generate + persist one on first run (stable thereafter). */
function loadOrCreateIdentity(channelDir: string): Identity {
  const idPath = path.join(channelDir, "identity.json");
  if (fs.existsSync(idPath)) return JSON.parse(fs.readFileSync(idPath, "utf8"));

  const { privateKey, publicKey } = crypto.generateKeyPairSync("ec", { namedCurve: "secp256k1" });
  const identity: Identity = {
    did: sandboxDid(path.basename(channelDir)),
    environment: "sandbox",
    created_at: new Date().toISOString(),
    privateKeyPem: privateKey.export({ type: "pkcs8", format: "pem" }),
    publicKeyJwk: publicKey.export({ format: "jwk" }),
  };
  fs.writeFileSync(idPath, JSON.stringify(identity, null, 2));
  return identity;
}

function signedRecord(did: string, uri: string, val: string, privateKeyPem: string): LabelRecord {
  const unsigned = { ver: 1, src: did, uri, val, cts: new Date().toISOString(), neg: false };
  const key = crypto.createPrivateKey(privateKeyPem);
  const sig: string = crypto.sign(null, Buffer.from(canonical(unsigned), "utf8"), key).toString("base64url");
  return { ...unsigned, sig };
}

function readStdin(): Promise<string> {
  return new Promise((resolve, reject) => {
    let data = "";
    process.stdin.setEncoding("utf8");
    process.stdin.on("data", (chunk) => (data += chunk));
    process.stdin.on("end", () => resolve(data));
    process.stdin.on("error", reject);
  });
}

async function main(): Promise<void> {
  const bundleDir = process.argv[2];
  if (!bundleDir) throw new Error("usage: execute.js <bundleDir>  (posts on stdin)");
  const channelDir = path.dirname(bundleDir);

  const spec: LabelerSpec = JSON.parse(fs.readFileSync(path.join(bundleDir, "labeler.spec.json"), "utf8"));
  const identity = loadOrCreateIdentity(channelDir);
  const input = JSON.parse(await readStdin()) as ExecInput;

  const compiled = (spec.labels || []).filter((l) => l.rule).map(compileLabel);
  const posts = input.posts || [];
  // Seeded with every label that COULD fire, so a label that matched nothing reports 0 rather than
  // vanishing from the summary. Without this, "my label isn't listed" and "my label doesn't exist"
  // look identical to the group — and a rule that silently matches nothing is the failure most
  // worth surfacing. Labels with no rule aren't compiled, so they stay absent by design: that's a
  // spec-level gap (see spec.warnings), not a run result.
  const perLabel: Record<string, number> = {};
  for (const label of compiled) perLabel[label.identifier] = 0;
  const examples: { handle?: string; val: string; text: string }[] = [];
  const lines: string[] = [];

  for (const post of posts) {
    const subject = { text: post.text || "", account: post.account };
    for (const label of compiled) {
      if (evaluateLabel(label, subject).fired.length === 0) continue;
      const rec = signedRecord(identity.did, post.uri || "", label.identifier, identity.privateKeyPem);
      lines.push(JSON.stringify(rec));
      perLabel[label.identifier] = (perLabel[label.identifier] || 0) + 1;
      if (examples.length < 5) {
        examples.push({ handle: post.handle, val: label.identifier, text: (post.text || "").slice(0, 140) });
      }
    }
  }

  const recordsPath = path.join(bundleDir, "labels.jsonl");
  fs.writeFileSync(recordsPath, lines.length ? lines.join("\n") + "\n" : "");

  process.stdout.write(JSON.stringify({
    status: "succeeded",
    did: identity.did,
    total: posts.length,
    records_emitted: lines.length,
    per_label: perLabel,
    examples,
    records_path: recordsPath,
  }));
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
