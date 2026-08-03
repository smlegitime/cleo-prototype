"""
Validation and feasibility checks for classification-rule signals.

This module is the single source of truth for what the labeler executor can actually
enforce. That executor supports exactly three signal types: keyword, pattern (regex),
and account (metadata threshold). There is deliberately NO semantic/LLM signal type:
labels whose intent can only be judged from tone or meaning are out of scope for this
version, so rules that lean on such signals must be caught before they are staged for
group approval rather than silently failing to enforce at label time.

Two entry points:
  - `signal_error(signal)`  -> a human-readable problem string, or None if the signal
    is enforceable. Used by finalize_rules to give CLEO actionable feedback so it can
    self-correct.
  - `sanitize_rules(rules)` -> (cleaned_rules, errors). Drops unenforceable signals and
    skips infeasible labels (no valid include signal). Used at the staging boundary so
    nothing the executor can't run reaches pending_classification_rules.
"""

import re

# Account-signal grammar — mirrored in plain language in RULES_DERIVATION_PROMPT.
# Keep this list and the prompt in sync; this is the machine-readable copy.
NUMERIC_ACCOUNT_FIELDS = frozenset({
    "account_age_days",
    "follower_count",
    "following_count",
    "post_count",
})
BOOLEAN_ACCOUNT_FIELDS = frozenset({
    "has_avatar",
    "has_description",
})
ACCOUNT_FIELDS = NUMERIC_ACCOUNT_FIELDS | BOOLEAN_ACCOUNT_FIELDS
ACCOUNT_OPERATORS = frozenset({"<", "<=", ">", ">=", "==", "!="})

# Guardrails for LLM-authored regex: cap length and reject patterns that match
# essentially everything (which would label the whole firehose).
MAX_PATTERN_LENGTH = 200
_MATCH_EVERYTHING = frozenset({".*", ".+", ".*?", ".+?", "(.*)", "(.+)", "^.*$", "^.*", ".*$"})


def _keyword_error(value: str) -> str | None:
    # A keyword is matched literally, so any non-empty text is enforceable. The empty
    # case is handled by the caller.
    return None


def _pattern_error(value: str) -> str | None:
    if len(value) > MAX_PATTERN_LENGTH:
        return f"pattern is too long ({len(value)} chars, max {MAX_PATTERN_LENGTH})"
    if value in _MATCH_EVERYTHING:
        return f"pattern '{value}' matches every post — narrow it to the target content"
    try:
        re.compile(value)
    except re.error as exc:
        return f"invalid regex '{value}': {exc}"
    return None


def _account_error(value: str) -> str | None:
    parts = value.split()
    if len(parts) != 3:
        return (
            f"account signal '{value}' must read '<field> <op> <threshold>', "
            "e.g. 'account_age_days < 30'"
        )
    field, op, threshold = parts
    if field not in ACCOUNT_FIELDS:
        return f"unknown account field '{field}' (allowed: {', '.join(sorted(ACCOUNT_FIELDS))})"
    if op not in ACCOUNT_OPERATORS:
        return f"unknown operator '{op}' (allowed: {', '.join(sorted(ACCOUNT_OPERATORS))})"
    if field in NUMERIC_ACCOUNT_FIELDS:
        try:
            float(threshold)
        except ValueError:
            return f"account field '{field}' needs a numeric threshold, got '{threshold}'"
    else:  # boolean field
        if threshold.lower() not in {"true", "false"}:
            return f"account field '{field}' needs a true/false threshold, got '{threshold}'"
        if op not in {"==", "!="}:
            return f"boolean field '{field}' only supports == or != (got '{op}')"
    return None


_VALIDATORS = {
    "keyword": _keyword_error,
    "pattern": _pattern_error,
    "account": _account_error,
}


def signal_error(signal: dict) -> str | None:
    """Return a human-readable problem with `signal`, or None if it is enforceable."""
    stype = signal.get("type")
    value = (signal.get("value") or "").strip()
    if stype not in _VALIDATORS:
        return f"unknown signal type '{stype}' (allowed: keyword, pattern, account)"
    if not value:
        return f"{stype} signal has an empty value"
    # A pattern is shown to the group only through its plain_name — the group votes on these
    # rules and cannot read a regex, so an unnamed pattern is not approvable.
    if stype == "pattern" and not (signal.get("plain_name") or "").strip():
        return (
            f"pattern '{value}' has no plain name — give it a short plain-language name the group "
            "can read, e.g. 'a cure word'"
        )
    return _VALIDATORS[stype](value)


def _read_include_groups(rule: dict) -> list[list[dict]]:
    """Normalize a rule's include side to a list of AND-groups.

    Accepts the legacy flat `include_signals` shape, where each signal was independently
    OR'd — i.e. exactly N groups of one.
    """
    groups = rule.get("include_groups")
    if groups:
        return [(g.get("all_of") if isinstance(g, dict) else g) or [] for g in groups]
    return [[sig] for sig in (rule.get("include_signals") or [])]


def sanitize_rule(rule: dict) -> tuple[dict, list[str]]:
    """Drop unenforceable signals from a single rule.

    Returns (cleaned_rule, errors). Each error is prefixed with the label identifier.
    A rule with no surviving include group is infeasible: cleaned_rule is returned but an
    error is recorded, and callers should skip staging it (see sanitize_rules).

    A group is AND-ed, so dropping one of its signals would silently WIDEN the rule — the
    remaining signals would fire on their own. An unenforceable signal therefore takes its
    whole group with it, rather than leaving a broader rule than the group approved.
    """
    identifier = rule.get("label_identifier") or "?"
    errors: list[str] = []

    def _keep_flat(signals: list[dict], kind: str) -> list[dict]:
        kept = []
        for sig in signals or []:
            err = signal_error(sig)
            if err is None:
                kept.append(sig)
            else:
                errors.append(f"[{identifier}] {kind} {err}")
        return kept

    kept_groups: list[dict] = []
    for group in _read_include_groups(rule):
        if not group:
            continue
        group_errors = [e for e in (signal_error(sig) for sig in group) if e]
        if group_errors:
            for err in group_errors:
                errors.append(f"[{identifier}] include: {err}")
            continue  # drop the whole group: see docstring
        kept_groups.append({"all_of": list(group)})

    cleaned = {
        "label_identifier": rule.get("label_identifier"),
        "include_groups": kept_groups,
        "exclude_signals": _keep_flat(rule.get("exclude_signals"), "exclude:"),
        "notes": rule.get("notes"),
    }

    if not kept_groups:
        errors.append(
            f"[{identifier}] has no enforceable include signal — this label can't be caught "
            "with keyword, pattern, or account signals and should be narrowed or dropped"
        )
    return cleaned, errors


def sanitize_rules(rules: list[dict]) -> tuple[list[dict], list[str]]:
    """Validate a batch of rules for staging.

    Returns (cleaned_rules, errors). Infeasible rules (no surviving include group) are
    omitted from cleaned_rules so they never reach pending_classification_rules; their
    reason is still recorded in errors.
    """
    cleaned_rules: list[dict] = []
    all_errors: list[str] = []
    for rule in rules or []:
        cleaned, errors = sanitize_rule(rule)
        all_errors.extend(errors)
        if cleaned["include_groups"]:
            cleaned_rules.append(cleaned)
    return cleaned_rules, all_errors
