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


def evaluate_corpus(
    spec: LabelerSpec, posts: list[dict], *, timeout: float = 60.0
) -> list[tuple[list[str], dict[str, list[str]]]]:
    """Run the canonical interpreter over `posts`, returning (fired label ids, why) per post.

    `why` maps a fired label id to the signal descriptions that carried it (e.g. ["cortisol +
    detox"]) — what the report needs to name the actual word behind a fire. Older engine builds
    predate the field, so a missing `matched` degrades to an empty dict rather than raising: the
    counts stay correct and only the explanations go quiet.

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
    return [(r.get("fired", []), r.get("matched") or {}) for r in data.get("results", [])]


def _humanize(identifier: str) -> str:
    """`ratified_win` -> `Ratified Win`. The fallback when a label carries no locale name."""
    return identifier.replace("_", " ").title()


def _label_name(label: dict) -> str:
    """The group's own name for a label, preferring the English locale, else its identifier."""
    locales = label.get("locales") or []
    loc = next((l for l in locales if l.get("lang") == "en"), None) or (locales[0] if locales else {})
    return (loc.get("name") or "").strip() or _humanize(label.get("identifier") or "label")


def _snippet(text: str) -> str:
    """Trim a post to _SNIPPET characters at a WORD boundary, with an ellipsis if anything was cut.

    Slicing blind produced quotes that ended mid-word ("a better work-", "coming from the bar"),
    which a group reads as a broken system rather than a truncated quote — and "the bar" for
    "the bargaining table" actively changes what the post appears to say.
    """
    flat = (text or "").replace("\n", " ").strip()
    if len(flat) <= _SNIPPET:
        return flat
    cut = flat[:_SNIPPET]
    # Only honour the word boundary if one is reasonably near the end; a long unbroken run
    # (a URL, say) would otherwise lose most of the snippet to the last space far behind it.
    space = cut.rfind(" ")
    if space > _SNIPPET * 0.6:
        cut = cut[:space]
    return cut.rstrip(" ,.;:—-") + "…"


def _example(post: dict, why: list[str] | None = None) -> dict:
    return {
        "handle": post.get("handle"),
        "text": _snippet(post.get("text") or ""),
        "query": post.get("query"),
        "bucket": post.get("bucket"),
        # What actually carried the fire, e.g. ["cortisol + detox"]. Empty when the engine build
        # predates the `matched` field (see evaluate_corpus).
        "why": list(why or []),
    }


def build_quality_report(spec: LabelerSpec, posts: list[dict]) -> dict:
    """Evaluate the corpus and aggregate into a report dict (see format_report_summary for the shape)."""
    evaluated = evaluate_corpus(spec, posts)
    n = min(len(posts), len(evaluated))

    rule_labels = [l for l in (spec.get("labels") or []) if l.get("rule")]
    # The group's own wording for each label, so the report can say "Ratified Win" rather than
    # `ratified_win`. Resolved here because this is where the spec is in scope; the formatter
    # only ever sees the report dict.
    per_label: dict[str, dict] = {
        l["identifier"]: {"name": _label_name(l), "count": 0, "examples": []} for l in rule_labels
    }
    matched_any = 0
    fp_candidates: list[dict] = []

    def slot_for(lid: str) -> dict:
        return per_label.setdefault(lid, {"name": _humanize(lid), "count": 0, "examples": []})

    for i in range(n):
        fired, why_by_label = evaluated[i]
        if not fired:
            continue
        matched_any += 1
        post = posts[i]
        for lid in fired:
            slot = slot_for(lid)
            slot["count"] += 1
            if len(slot["examples"]) < _MAX_EXAMPLES:
                slot["examples"].append(_example(post, why_by_label.get(lid)))
        # A 'context' post is an on-topic/benign query result; firing there is a possible false
        # positive. NOTE: this heuristic assumes TEXT-matching semantics and is misleading for
        # account-threshold rules — a context post firing 'follower_count >= 100' is a correct fire,
        # not an FP. See the module docstring's KNOWN LIMITATION; revisit with a distributional report.
        if post.get("bucket") == "context" and len(fp_candidates) < _MAX_FP_CANDIDATES:
            fp_candidates.append({
                **_example(post),
                "labels": [per_label[lid]["name"] for lid in fired if lid in per_label],
                # Flattened across labels: on a false-positive candidate the question is which
                # wording caught an ordinary post, not which label it was filed under.
                "why": [w for lid in fired for w in why_by_label.get(lid, [])],
            })

    return {
        "total": n,
        "matched_any": matched_any,
        "matched_none": n - matched_any,
        "per_label": per_label,
        "false_positive_candidates": fp_candidates,
    }


# How the corpus was built, in the group's terms. Load-bearing, not throat-clearing: derive_queries
# stocks the corpus from the rules' OWN signal text (up to 32 trigger queries against 8 context
# ones), so "fired on 99 of 200" is 99 of the posts most likely to fire — not 99 of Bluesky. Without
# this line a group reads its labeler as vastly wider-reaching than it is, and any fire count looks
# alarming or reassuring for the wrong reason.
_CORPUS_NOTE = (
    "I searched Bluesky for posts using the words your rules look for, then ran your rules "
    "over the **{total} posts** that came back. These aren't random posts — they're the ones "
    "most likely to be caught, so the numbers run high on purpose."
)

# The one verdict this report can honestly reach. A high fire count proves nothing (see
# _CORPUS_NOTE), but a label that fired ZERO times failed on a corpus stocked with its own search
# terms — which is close to conclusive that the rule is looking for wording nobody writes. This is
# the failure that currently renders identically to success, and the one groups can't spot unaided.
_NEVER_FIRED = (
    "⚠️ **{name} didn't catch anything.** I went looking for posts using its own words and it "
    "still didn't fire on a single one — that usually means the rule is looking for wording "
    "people don't actually use. Worth fixing before you go further."
)

_MAX_SHOWN_EXAMPLES = 1   # per label, in chat — the report keeps more for the quality screen


def _why_suffix(why: list[str] | None) -> str:
    """` — matched “cortisol + detox”`, or empty when the engine build didn't report it.

    Kept on the attribution line rather than given its own: a bare newline is a SOFT break in
    markdown and collapses into the line above, so anything that must stand alone has to be a
    list item or a paragraph. Trailing the attribution costs nothing and can't collapse wrongly.
    """
    parts = [w for w in (why or []) if w]
    return f" — matched “{'”, “'.join(parts)}”" if parts else ""


def format_report_summary(report: dict) -> str:
    """Render a report dict as a chat message a non-technical group can actually read.

    Every block is a separate paragraph, because the chat renders standard markdown: a single
    newline is a soft break that collapses into the line above, which is what turned the previous
    version into a wall. Anything that must stand on its own line is a list item or a paragraph.
    Emphasis is **double-asterisk** for the same reason — a single asterisk is italic there.
    """
    total = report["total"]
    per_label = report["per_label"]

    blocks = ["**🔎 Rule-quality check**", _CORPUS_NOTE.format(total=total)]

    counts = [f"- **{slot['name']}** — {slot['count']} of {total}" for slot in per_label.values()]
    counts.append(f"- Caught by nothing — {report['matched_none']} of {total}")
    blocks.append("**What each label caught**")
    blocks.append("\n".join(counts))

    silent = [slot["name"] for slot in per_label.values() if not slot["count"]]
    blocks.extend(_NEVER_FIRED.format(name=name) for name in silent)

    examples = []
    for slot in per_label.values():
        for ex in slot["examples"][:_MAX_SHOWN_EXAMPLES]:
            examples.append(
                f"**{slot['name']}** · @{ex['handle']}{_why_suffix(ex.get('why'))}\n> {ex['text']}"
            )
    if examples:
        blocks.append("**A couple it caught**")
        blocks.extend(examples)

    fps = report["false_positive_candidates"]
    if fps:
        blocks.append(
            "**⚠️ These were ordinary on-topic posts, and got caught anyway.** "
            "Worth checking whether you'd want them labelled."
        )
        blocks.extend(
            f"@{fp['handle']} → {', '.join(fp['labels'])}"
            f"{_why_suffix(fp.get('why'))}\n> {fp['text']}"
            for fp in fps[:3]
        )

    blocks.append(
        "Does this look like what you meant to catch? Tell me what's off and I'll update the "
        "rules and run this again."
    )
    return "\n\n".join(blocks)
