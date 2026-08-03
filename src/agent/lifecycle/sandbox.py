"""
Sandbox-executor bridge — shells to the labeler-engine `execute` entrypoint over a materialized
bundle, the `deploy` stage's end-to-end run.

Like quality.evaluate_corpus this only marshals JSON in/out of the Node subprocess and never
reimplements evaluation. The executor (labeler-engine/dist/execute.js) loads the bundle's spec + the
channel's persistent sandbox identity, evaluates the replay corpus, signs a label record per fired
(post, label) with a locally-generated key, writes them to <bundleDir>/labels.jsonl, and returns a
summary. Nothing is published — the did:web identity is a placeholder and records go to disk only.

Requires `node` on PATH (or NODE_BIN) and the build at labeler-engine/dist/execute.js
(rebuild: ./scripts/build_labeler_engine.sh).
"""

import json
import logging
import os
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)

NODE_BIN = os.environ.get("NODE_BIN", "node")
EXECUTE_JS = Path(__file__).resolve().parents[3] / "labeler-engine" / "dist" / "execute.js"


def _exec_post(post: dict) -> dict:
    """Marshal a corpus post into the executor's post shape: uri + text (+ account when enriched).

    The executor needs the `uri` (the label record's subject) and `handle` (for the run report's
    examples) on top of the interpreter's usual text/account — so this is a superset of quality._subject.
    """
    out = {"uri": post.get("uri"), "text": post.get("text", ""), "handle": post.get("handle")}
    account = post.get("account")
    if account:
        out["account"] = account
    return out


def run_executor(bundle_dir: str, posts: list[dict], *, timeout: float = 90.0) -> dict:
    """Run the sandbox executor over `posts` for a materialized bundle; return its summary dict.

    Summary shape: {status, did, total, records_emitted, per_label, examples, records_path}.
    Raises FileNotFoundError if the engine isn't built, RuntimeError if the subprocess fails.
    """
    if not EXECUTE_JS.exists():
        raise FileNotFoundError(
            f"labeler-engine execute build missing at {EXECUTE_JS} — run ./scripts/build_labeler_engine.sh"
        )
    payload = json.dumps({"posts": [_exec_post(p) for p in posts]})
    proc = subprocess.run(
        [NODE_BIN, str(EXECUTE_JS), bundle_dir],
        input=payload, capture_output=True, text=True, timeout=timeout,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"sandbox executor failed: {proc.stderr.strip()[:500]}")
    return json.loads(proc.stdout)
