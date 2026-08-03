"""
Rule-quality evaluation over a real-post corpus, for the generate stage's chat report.

The evaluation itself is delegated to the ONE canonical interpreter (labeler-engine) via its Node
batch entrypoint — never reimplemented in Python, which would be exactly the divergence the
client-side matcher deletion killed. This module only marshals JSON in/out of that subprocess and
aggregates the per-post results into a report a group can read: how often each label fired, some
examples, and posts that fired despite being on-topic/benign (false-positive candidates).

Requires `node` on PATH (or NODE_BIN) and the labeler-engine build at labeler-engine/dist/batch.js
(rebuild: frontend/node_modules/.bin/tsc -p labeler-engine/tsconfig.build.json).

**KNOWN LIMITATION** — account-threshold rules are NOT meaningfully covered by this report (revisit
later). The corpus is sampled by TEXT relevance (see corpus.derive_queries), and this report
measures a text-intent gap: how often a rule fires, and on-topic/benign posts that fired (candidate
false positives). Neither lens fits a deterministic account signal like `follower_count >= 100`:
  * The rule is objective — an account has >=100 followers or it doesn't, so there is no
    precision/recall-against-meaning to measure; the real question is whether the THRESHOLD is well
    calibrated, which needs a follower-count DISTRIBUTION, not a fire count.
  * The corpus authors are whoever happened to post text matching the (context) queries, so the fire
    count reflects that incidental sample, not a representative one.
  * The false-positive heuristic below (context-bucket posts that fired) actively MISLEADS here: a
    context post firing an account rule just means its author genuinely crosses the threshold — a
    correct fire, not a false positive.
Account rules still run correctly at label time; only this pre-deploy report is uninformative for
them. A real fix couldbe a separate DISTRIBUTIONAL report over the enriched authors, not this one.
"""

import json
import logging
import os
import subprocess
from pathlib import Path

from src.agent.spec import LabelerSpec

logger = logging.getLogger(__name__)

NODE_BIN = os.environ.get("NODE_BIN", "node")
BATCH_JS = Path(__file__).resolve().parents[3] / "labeler-engine" / "dist" / "batch.js"

_MAX_EXAMPLES = 3          # example fired posts kept per label
_MAX_FP_CANDIDATES = 5     # on-topic/benign posts that fired, surfaced as FP candidates
_SNIPPET = 140


def _subject(post: dict) -> dict:
    """Convert a corpus post into the interpreter's Subject shape.

    Always carries `text`; includes the `account` block only when the post was enriched (see
    corpus.enrich_accounts), so keyword/pattern-only corpora send the same lean payload as before.
    """
    subject = {"text": post.get("text", "")}
    account = post.get("account")
    if account:
        subject["account"] = account
    return subject


def evaluate_corpus(spec: LabelerSpec, posts: list[dict], *, timeout: float = 60.0) -> list[list[str]]:
    """Run the canonical interpreter over `posts`, returning the fired label ids per post (by index).

    Raises FileNotFoundError if the engine isn't built, or RuntimeError if the subprocess fails.
    """
    if not BATCH_JS.exists():
        raise FileNotFoundError(
            f"labeler-engine batch build missing at {BATCH_JS} — run "
            "`frontend/node_modules/.bin/tsc -p labeler-engine/tsconfig.build.json`"
        )
    payload = json.dumps({"spec": spec, "posts": [_subject(p) for p in posts]})
    proc = subprocess.run(
        [NODE_BIN, str(BATCH_JS)],
        input=payload, capture_output=True, text=True, timeout=timeout,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"labeler-engine batch eval failed: {proc.stderr.strip()[:500]}")
    data = json.loads(proc.stdout)
    return [r.get("fired", []) for r in data.get("results", [])]


def _example(post: dict) -> dict:
    return {
        "handle": post.get("handle"),
        "text": (post.get("text") or "").replace("\n", " ")[:_SNIPPET],
        "query": post.get("query"),
        "bucket": post.get("bucket"),
    }


def build_quality_report(spec: LabelerSpec, posts: list[dict]) -> dict:
    """Evaluate the corpus and aggregate into a report dict (see format_report_summary for the shape)."""
    fired_per_post = evaluate_corpus(spec, posts)
    n = min(len(posts), len(fired_per_post))

    label_ids = [l["identifier"] for l in (spec.get("labels") or []) if l.get("rule")]
    per_label: dict[str, dict] = {lid: {"count": 0, "examples": []} for lid in label_ids}
    matched_any = 0
    fp_candidates: list[dict] = []

    for i in range(n):
        fired = fired_per_post[i]
        if not fired:
            continue
        matched_any += 1
        post = posts[i]
        for lid in fired:
            slot = per_label.setdefault(lid, {"count": 0, "examples": []})
            slot["count"] += 1
            if len(slot["examples"]) < _MAX_EXAMPLES:
                slot["examples"].append(_example(post))
        # A 'context' post is an on-topic/benign query result; firing there is a possible false
        # positive. NOTE: this heuristic assumes TEXT-matching semantics and is misleading for
        # account-threshold rules — a context post firing 'follower_count >= 100' is a correct fire,
        # not an FP. See the module docstring's KNOWN LIMITATION; revisit with a distributional report.
        if post.get("bucket") == "context" and len(fp_candidates) < _MAX_FP_CANDIDATES:
            fp_candidates.append({**_example(post), "labels": fired})

    return {
        "total": n,
        "matched_any": matched_any,
        "matched_none": n - matched_any,
        "per_label": per_label,
        "false_positive_candidates": fp_candidates,
    }


def format_report_summary(report: dict) -> str:
    """Render a report dict as a concise chat message."""
    total = report["total"]
    lines = [f"🔎 *Rule-quality check* on {total} real Bluesky posts:"]
    for lid, slot in report["per_label"].items():
        lines.append(f"• `{lid}` — fired on {slot['count']}/{total}")
    lines.append(f"• {report['matched_none']}/{total} matched no label")

    examples = []
    for lid, slot in report["per_label"].items():
        for ex in slot["examples"][:2]:
            examples.append(f"  – `{lid}` ← @{ex['handle']}: “{ex['text']}”")
    if examples:
        lines.append("\n*Examples that fired:*")
        lines.extend(examples)

    fps = report["false_positive_candidates"]
    if fps:
        lines.append("\n⚠️ *On-topic posts that fired* (check these for false positives):")
        for fp in fps[:3]:
            lines.append(f"  – @{fp['handle']}: “{fp['text']}” → {', '.join(fp['labels'])}")

    lines.append("\nWant to adjust anything? Tell me and we'll update the rules, then re-check.")
    return "\n".join(lines)
