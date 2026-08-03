"""
Mock feed generator for the preview stage.

Given a labeler spec (from spec.build_spec) and the group's conversation, produce ~10 realistic
Bluesky-style sample posts in the community's domain that exercise the rules — some that clearly
trip each label, some near-misses (only half of an AND-group, or a term the label excludes), and
some benign on-topic posts. The frontend evaluates matching itself, so these are just content; the
posts only need to be *relevant* and to *contain* the kinds of wording the rules look for.

Generation is deterministic (tool_model runs at temperature 0), which pairs with the spec_id-keyed
cache in the API: the same design yields the same feed until the rules change.
"""

import logging

from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, Field

from src.agent.spec import LabelerSpec, SpecLabel, SpecSignal
from src.config import tool_model

logger = logging.getLogger(__name__)

NUM_POSTS = 10


class _GenPost(BaseModel):
    name: str = Field(description="Author display name, e.g. 'Coach Ryan' or 'wellnessbyheather'")
    handle: str = Field(description="Author handle without the @, e.g. 'ryancoaches'")
    text: str = Field(description="The post body: 1-3 natural sentences, like a real Bluesky post")
    media: bool = Field(description="true if the post would include an image, false otherwise")


class _GenBatch(BaseModel):
    posts: list[_GenPost] = Field(description=f"Exactly {NUM_POSTS} posts covering the mix described")


def _describe_signal(sig: SpecSignal) -> str:
    kind, value, plain = sig["type"], sig["value"], sig.get("plain_name")
    if kind == "keyword":
        return f'the text "{value}"'
    if kind == "pattern":
        return f'{plain or "wording"} (text matching /{value}/)'
    return f'{plain or value} (an account trait — cannot appear in post text)'


def _describe_label(label: SpecLabel) -> str:
    loc = next((l for l in label["locales"] if l.get("lang") == "en"), None) or (
        label["locales"][0] if label["locales"] else None
    )
    name = (loc and loc.get("name")) or label["identifier"].replace("_", " ").title()
    desc = (loc and loc.get("description")) or ""

    rule = label.get("rule")
    lines = [f'- Label "{name}"' + (f": {desc}" if desc else "")]
    if not rule or not rule.get("include_groups"):
        lines.append("  (no rules yet)")
        return "\n".join(lines)

    lines.append("  Fires when ANY of these holds:")
    for group in rule["include_groups"]:
        parts = " AND ".join(_describe_signal(s) for s in group["all_of"])
        lines.append(f"    • {parts}")
    if rule.get("exclude_signals"):
        excl = "; ".join(_describe_signal(s) for s in rule["exclude_signals"])
        lines.append(f"  But NEVER when the post also contains: {excl}")
    return "\n".join(lines)


def _recent_human_texts(messages: list | None, limit: int = 8) -> list[str]:
    out: list[str] = []
    for m in messages or []:
        role = m.get("type") if isinstance(m, dict) else getattr(m, "type", None)
        content = m.get("content") if isinstance(m, dict) else getattr(m, "content", None)
        if role == "human" and isinstance(content, str) and content.strip():
            out.append(content.strip()[:200])
    return out[-limit:]


def _build_prompt(spec: LabelerSpec, messages: list | None) -> tuple[str, str]:
    labeler = spec["labeler"]
    name = labeler.get("display_name") or "this community's labeler"
    description = labeler.get("description") or ""
    rules_block = "\n".join(_describe_label(l) for l in spec["labels"])
    convo = _recent_human_texts(messages)
    convo_block = "\n".join(f"- {t}" for t in convo) if convo else "(no conversation captured)"

    system = (
        "You generate sample social-media posts to preview a Bluesky community labeler. The group "
        "will look at these posts to see how their labels behave BEFORE the labeler goes live. Write "
        "posts that read like real Bluesky posts from that community's topic area — natural, varied "
        "authors and voices, 1-3 sentences each.\n\n"
        f"Produce exactly {NUM_POSTS} posts with this mix:\n"
        "  • For EACH label, at least one post whose wording clearly matches its rule (include the "
        "actual triggering words/phrases so the label would fire).\n"
        "  • A few near-misses: posts that match only part of an AND-rule, or that contain an "
        "excluded term, so the label should NOT fire — these show the rules holding back.\n"
        "  • A few benign, on-topic posts that no label should touch.\n"
        "Do not mention labels, rules, or moderation in the posts themselves. Vary handles and names. "
        "Set media=true on roughly a third of them."
    )
    user = (
        f"Labeler: {name}\n"
        f"{description}\n\n"
        f"Labels and their rules:\n{rules_block}\n\n"
        f"What the group has said while designing it (for domain/voice):\n{convo_block}\n\n"
        f"Write the {NUM_POSTS} sample posts now."
    )
    return system, user


def generate_preview_posts(spec: LabelerSpec, messages: list | None = None) -> list[dict]:
    """Generate up to NUM_POSTS mock posts for the preview feed.

    Returns a list of {name, handle, text, media} dicts (matching state.PreviewPost). Returns an
    empty list on any failure so the caller can fall back to a static feed rather than break.
    """
    try:
        system, user = _build_prompt(spec, messages)
        structured = tool_model.with_structured_output(_GenBatch)
        result: _GenBatch = structured.invoke([SystemMessage(content=system), HumanMessage(content=user)])
        posts = [
            {"name": p.name, "handle": p.handle.lstrip("@"), "text": p.text, "media": bool(p.media)}
            for p in result.posts
        ]
        return posts[:NUM_POSTS]
    except Exception:
        logger.exception("Preview post generation failed; caller should fall back to static posts")
        return []
