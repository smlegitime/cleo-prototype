/**
 * Batch evaluation entrypoint — the ONE canonical interpreter run over a corpus.
 *
 * Reads `{ spec, posts }` JSON from stdin, evaluates every post against the spec's compiled labels,
 * and writes `{ results: [{ fired: string[] }] }` to stdout, aligned by index to the input posts.
 *
 * This is the same compileLabel/evaluateLabel that the preview UI and the deployed labeler use — the
 * Python `generate` step shells out to it so rule quality is measured by the real interpreter and
 * never a reimplementation (see the note in ./evaluate.ts). It is also the core the sandbox executor
 * will wrap, so it is deliberately I/O-thin: read stdin, evaluate, write stdout, nothing else.
 */

import { compileLabel, evaluateLabel } from "./index.js";
import type { AccountTraits, LabelerSpec } from "./index.js";

// Minimal ambient declaration for the Node globals used below, so the engine stays dependency-free
// (no @types/node, no node_modules — it is shared with the browser preview which must not pull Node).
declare const process: {
  stdin: { setEncoding(enc: string): void; on(ev: string, cb: (chunk: string) => void): void };
  stdout: { write(s: string): void };
  exit(code: number): void;
};

interface BatchInput {
  spec: LabelerSpec;
  posts: { text?: string; account?: AccountTraits }[];
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
  const input = JSON.parse(await readStdin()) as BatchInput;
  // Only labels with a rule can fire; rule-less labels are approved-but-undefined.
  const compiled = (input.spec.labels || []).filter((l) => l.rule).map(compileLabel);
  const results = (input.posts || []).map((post) => {
    const subject = { text: post.text || "", account: post.account };
    const fired = compiled
      .filter((label) => evaluateLabel(label, subject).fired.length > 0)
      .map((label) => label.identifier);
    return { fired };
  });
  process.stdout.write(JSON.stringify({ results }));
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
