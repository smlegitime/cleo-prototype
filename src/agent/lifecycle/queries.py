"""
Spec-agnostic search-query derivation for the `generate` lifecycle step.

Pure and offline: given ANY labeler spec (from `spec.build_spec`), mine its signals and
metadata for searchable phrases and stratify them into 'trigger' (from a rule signal) and
'context' (from domain/label metadata) buckets. Nothing here touches the network — the
authenticated fetch that consumes these queries lives in `corpus.py`.

Kept separate from the fetch layer so the pure derivation stays trivially testable and so the
enrichment seam (where new I/O-backed signal types land) has an unambiguous home next door.
"""

from __future__ import annotations

import hashlib
import logging
from typing import Literal, TypedDict

try:  # CPython's regex parser — internal but a stable API across 3.8–3.14
    from re import _parser as _sre_parse  # type: ignore[attr-defined]
except ImportError:  # pragma: no cover
    import sre_parse as _sre_parse  # type: ignore[no-redef]

from src.agent.spec import LabelerSpec, SpecLabel, SpecSignal

logger = logging.getLogger(__name__)

# Keep individual queries sane: skip 1-char noise and multi-sentence blobs that return nothing.
_MIN_QUERY_LEN = 2
_MAX_QUERY_WORDS = 6

QueryBucket = Literal["trigger", "context"]


class Query(TypedDict):
    q: str              # the search string
    bucket: QueryBucket  # 'trigger' = derived from a rule signal; 'context' = domain/metadata
    source: str         # what in the spec produced it, for traceability

# --------------------------------------------------------------------------------------------
# Query derivation — pure, offline, spec-agnostic
# --------------------------------------------------------------------------------------------

# --- regex-literal mining ---------------------------------------------------------------------
# A regex isn't searchable, and a pattern's plain_name ("a cure word", "attacking language") is a
# poor search proxy — it pulls meta-discussion, not real instances. Instead we mine the literal
# text baked into the pattern: alternation branches ("dm me"|"link in bio"), the mandatory
# literal spine (skipping optional groups, so `r[-_*]?e[-_*]?t...d` recovers "retard"), and
# whitespace runs (`\s+` -> a space, so "dm\s+me" -> "dm me").

_BREAK = "\x00"  # marks a non-literal boundary (wildcards, non-space classes) that splits phrases

# Function words that carry no search signal on their own; a mined phrase made only of these
# (e.g. the "you're / they're / such / what" harassment scaffold) is dropped as noise.
_STOPWORDS = {
    "a", "an", "the", "you", "your", "youre", "your", "they", "theyre", "theyr", "he", "hes",
    "she", "shes", "his", "her", "their", "them", "my", "me", "we", "it", "its", "this",
    "that", "what", "such", "is", "are", "be", "to", "of", "in", "on", "at", "and", "or",
    "for", "with", "so", "damn", "fucking", "as", "by", "i", "im", "ive", "ill", "id",
    "dont", "cant", "wont", "aint", "whats", "thats", "now",
}


def _is_space_class(items) -> bool:
    for iop, iarg in items:
        if getattr(iop, "name", "") == "CATEGORY" and getattr(iarg, "name", "") == "CATEGORY_SPACE":
            return True
    return False


def _walk_seq(tokens, cap: int = 48) -> list[str]:
    """Render a parsed regex sequence into candidate literal strings (branches expanded)."""
    results = [""]
    for op, av in tokens:
        parts = _token_parts(op, av) or [""]
        new: list[str] = []
        for r in results:
            for p in parts:
                new.append(r + p)
                if len(new) >= cap:
                    break
            if len(new) >= cap:
                break
        results = new
    return results


def _token_parts(op, av) -> list[str]:
    name = getattr(op, "name", "")
    if name == "LITERAL":
        return [chr(av)]
    if name == "AT":            # anchors (\b, ^, $) contribute nothing
        return [""]
    if name == "IN":            # char class: whitespace -> space, anything else -> boundary
        return [" "] if _is_space_class(av) else [_BREAK]
    if name == "BRANCH":        # (a|b|c) -> the union of each branch's renderings
        _, branches = av
        opts: list[str] = []
        for b in branches:
            opts.extend(_walk_seq(list(b)))
        return opts or [""]
    if name == "SUBPATTERN":    # (?...:) group -> its inner sequence (av[-1] across versions)
        return _walk_seq(list(av[-1])) or [""]
    if name in ("MAX_REPEAT", "MIN_REPEAT"):
        mn, _mx, sub = av
        return [""] if mn == 0 else (_walk_seq(list(sub)) or [""])  # optional -> mandatory spine
    return [_BREAK]             # ANY, NOT_LITERAL, backrefs, etc. break the literal run


def _is_contentful(phrase: str) -> bool:
    return any(w not in _STOPWORDS and len(w) >= 2 for w in phrase.lower().split())


def _content_words(phrase: str) -> tuple[str, ...]:
    return tuple(sorted(w for w in phrase.split() if w not in _STOPWORDS))


def _mine_literals(pattern: str, *, max_per_pattern: int = 6) -> list[str]:
    """Extract searchable literal phrases from a regex, best (most contentful) first.

    Phrases that differ only by stopwords (e.g. "cure my autism" vs "cure their autism") collapse
    to one, so a single pronoun-heavy pattern can't crowd distinct signals out of the query budget.
    """
    try:
        tree = list(_sre_parse.parse(pattern))
    except Exception:
        return []
    phrases: list[str] = []
    seen: set[str] = set()
    for cand in _walk_seq(tree):
        for piece in cand.split(_BREAK):
            piece = " ".join(piece.split()).strip()  # collapse whitespace runs
            key = piece.lower()
            if piece and key not in seen and _is_contentful(piece) and _acceptable(piece):
                seen.add(key)
                phrases.append(key)
    # Prefer phrases with more content words, then longer ones — the most specific queries.
    phrases.sort(key=lambda p: (len(_content_words(p)), len(p)), reverse=True)
    out: list[str] = []
    content_seen: set[tuple[str, ...]] = set()
    for p in phrases:
        content = _content_words(p)
        if content and content not in content_seen:
            content_seen.add(content)
            out.append(p)
        if len(out) >= max_per_pattern:
            break
    return out


def _signal_queries(sig: SpecSignal) -> list[str]:
    """Searchable query strings for a signal (may be several for patterns), or [] if none.

    keyword -> its literal value (incl. hashtags). pattern -> literals mined from the regex,
    falling back to the plain_name only when mining finds nothing (e.g. a pure-scaffold pattern).
    account -> [], since account traits never appear in post text.
    """
    kind = sig.get("type")
    value = (sig.get("value") or "").strip()
    plain = (sig.get("plain_name") or "").strip()
    if kind == "keyword":
        return [value] if value else []
    if kind == "pattern":
        mined = _mine_literals(value)
        if mined:
            return mined
        return [plain] if plain else []
    return []


def _acceptable(q: str) -> bool:
    return len(q.strip()) >= _MIN_QUERY_LEN and len(q.split()) <= _MAX_QUERY_WORDS


def _label_context_terms(label: SpecLabel) -> list[str]:
    """Concise on-topic terms from a label's locales (name is short; description often isn't)."""
    terms: list[str] = []
    for loc in label.get("locales") or []:
        name = (loc.get("name") or "").strip()
        if name:
            terms.append(name)
    return terms


def derive_queries(
    spec: LabelerSpec,
    *,
    max_trigger: int = 32,
    max_context: int = 8,
) -> list[Query]:
    """Derive a stratified, deduped query set from the spec alone.

    trigger queries come from every rule signal that carries searchable text (include AND
    exclude signals — searching excluded terms exercises the suppression path and surfaces
    false-positive bait). context queries come from labeler + label metadata to pull on-topic
    and benign posts. Deduped case-insensitively; trigger wins ties over context.
    """
    seen: set[str] = set()
    trigger: list[Query] = []
    context: list[Query] = []

    def add(bucket: QueryBucket, q: str | None, source: str, sink: list[Query]) -> None:
        if not q:
            return
        q = q.strip()
        key = q.lower()
        if key in seen or not _acceptable(q):
            return
        seen.add(key)
        sink.append({"q": q, "bucket": bucket, "source": source})

    for label in spec.get("labels") or []:
        ident = label.get("identifier") or "?"
        rule = label.get("rule")
        if rule:
            for group in rule.get("include_groups") or []:
                for sig in group.get("all_of") or []:
                    for q in _signal_queries(sig):
                        add("trigger", q, f"{ident}:include", trigger)
            for sig in rule.get("exclude_signals") or []:
                for q in _signal_queries(sig):
                    add("trigger", q, f"{ident}:exclude", trigger)

    # Context: labeler metadata first, then per-label names.
    labeler = spec.get("labeler") or {}
    add("context", (labeler.get("display_name") or "").strip() or None, "labeler:name", context)
    for label in spec.get("labels") or []:
        ident = label.get("identifier") or "?"
        for term in _label_context_terms(label):
            add("context", term, f"{ident}:name", context)

    return trigger[:max_trigger] + context[:max_context]


def corpus_key(spec: LabelerSpec) -> str:
    """A fingerprint of the query BASIS (not the spec_id), for caching the fetched corpus.

    Two specs that would search for the same things share a key, so tweaking a rule's structure
    (e.g. splitting an OR-group) without changing the searchable terms reuses the cached posts.
    """
    qs = sorted(q["q"].lower() for q in derive_queries(spec))
    digest = hashlib.sha256("\n".join(qs).encode("utf-8")).hexdigest()
    return f"corpus:sha256:{digest}"
