_CIRCLED = "①②③④⑤⑥⑦⑧⑨⑩⑪⑫⑬⑭⑮⑯⑰⑱⑲⑳"


# Plain-language rendering of a label's (blurs, severity) pair
behavior_plain = {
    ("content", "alert"): "❌ Whole post hidden behind a ⚠️ danger warning — subscribers tap to view it",
    ("content", "inform"): "❌ Whole post hidden behind an ℹ️ neutral note — subscribers tap to view it",
    ("content", "none"): "❌ Whole post hidden — subscribers tap to view it, with no label saying why",
    ("media", "alert"): "📹 Photos & videos hidden behind a ⚠️ danger warning — the text stays visible",
    ("media", "inform"): "📹 Photos & videos hidden behind an ℹ️ neutral note — the text stays visible",
    ("media", "none"): "📹 Photos & videos hidden — the text stays visible, with no label saying why",
    ("none", "alert"): "⚠️ Danger warning label on the post — nothing is hidden",
    ("none", "inform"): "ℹ️ Neutral information label on the post — nothing is hidden",
    ("none", "none"): "◻️ No visual effect",
}


def _label_behavior(label: dict) -> str:
    """Describe what subscribers see for a label, from its (blurs, severity) pair."""
    return behavior_plain.get(
        (label.get("blurs"), label.get("severity")),
        f"blurs: {label.get('blurs')} | severity: {label.get('severity')}",
    )


_ACCOUNT_FIELD_LABELS = {
    "account_age_days": "account age in days",
    "follower_count": "follower count",
    "following_count": "following count",
    "post_count": "post count",
}
_ACCOUNT_OP_WORDS = {"<": "under", "<=": "at most", ">": "over", ">=": "at least", "==": "exactly", "!=": "not"}


def _account_signal_to_plain(value: str) -> str:
    """Translate an account signal ('<field> <op> <threshold>') into plain language."""
    parts = value.split()
    if len(parts) != 3:
        return value
    field, op, threshold = parts
    if field in ("has_avatar", "has_description"):
        thing = "profile picture" if field == "has_avatar" else "bio"
        wants_true = (threshold.lower() == "true") == (op == "==")
        return f"accounts with a {thing}" if wants_true else f"accounts with no {thing}"
    field_label = _ACCOUNT_FIELD_LABELS.get(field, field)
    return f"accounts with {field_label} {_ACCOUNT_OP_WORDS.get(op, op)} {threshold}"


def _signal_to_plain(signal: dict) -> str:
    """Render one signal in plain language, hiding regex/field syntax from the group.

    The model's own plain_name wins when present — it names the signal in the group's words
    ("a cure word") where the raw value is a regex nobody voting can read. Keyword and
    account signals read plainly on their own, so they fall back to their value.
    """
    plain_name = (signal.get("plain_name") or "").strip()
    if plain_name:
        return plain_name
    stype = signal.get("type")
    value = signal.get("value", "")
    if stype == "keyword":
        return f'"{value}"'
    if stype == "pattern":
        # Only reachable for legacy rules staged before plain names were required.
        return f"wording like `{value}`"
    if stype == "account":
        return _account_signal_to_plain(value)
    return value


def _condition_lines(groups: list[list[dict]]) -> list[str]:
    """Render include groups as numbered conditions a non-technical member can check.

    Single-signal groups are collected into one "mentions any of" condition — they are
    plain alternatives and numbering each separately buries the ones that matter. Groups
    with several signals get a condition each, with the AND spelled out on its own line:
    "flags a cure word AND a sales pitch" and "flags a cure word OR a sales pitch" are one
    word apart and describe very different labelers, so the difference cannot be left to a
    comma. A group approving what it misread is worse than a group not voting at all.
    """
    singles = [g[0] for g in groups if len(g) == 1]
    multis = [g for g in groups if len(g) > 1]
    lines: list[str] = []
    n = 0

    if singles:
        lines.append(f"\n  {_CIRCLED[n]} the post mentions any of:")
        lines.append(f"     {', '.join(_signal_to_plain(s) for s in singles)}")
        n += 1

    for group in multis:
        if n >= len(_CIRCLED):
            lines.append(f"\n  · …and {len(multis) - n} more condition(s)")
            break
        joiner = "BOTH" if len(group) == 2 else f"ALL {len(group)} of"
        lines.append(f"\n  {_CIRCLED[n]} the post mentions {joiner}:")
        for i, sig in enumerate(group):
            lines.append(f"     {'·' if i == 0 else '· AND'} {_signal_to_plain(sig)}")
        n += 1
    return lines

def _include_groups(rule: dict) -> list[list[dict]]:
    """Read a rule's include side as AND-groups, oldest shape included.

    Legacy rules stored a flat `include_signals` list whose signals each fired
    independently — semantically N groups of one, which is what this returns for them.
    """
    groups = rule.get("include_groups")
    if groups:
        return [(g.get("all_of") if isinstance(g, dict) else g) or [] for g in groups]
    return [[sig] for sig in (rule.get("include_signals") or [])]

# Utility functions for summarize_conversation and draft_response
def format_proposal_block(proposal: dict) -> str:
    """Render a pending proposal using markdown formatting for dark-theme legibility."""
    lines = ["📋 **Proposed update**", "─" * 15, "\n"]

    if proposal.get("display_name"):
        lines.append(f"**Labeler name:** {proposal['display_name']}\n")
    if proposal.get("description"):
        lines.append(f"**Description:** {proposal['description']}")

    if proposal.get("labels"):
        lines.append("\n**Labels:**")
        for label in proposal["labels"]:
            locales = label.get("locales") or []
            name = locales[0].get("name") if locales else label.get("identifier")
            description = locales[0].get("description") if locales else None
            lines.append(f"\n• **{name}**") #`{label.get('identifier')}`")
            lines.append(f"  {_label_behavior(label)}")
            if description:
                lines.append(f"  *{description}*")

    lines += ["\nReact with 👍🏾 to approve this change.", "─" * 30]
    return "\n".join(lines)

def format_rules_context(rules: dict) -> str:
    """Compact rendering of the current classification rules for the rules-derivation prompt.

    Prompt-only (never shown to the group), so it lists signal types/values precisely — this
    is what lets the feedback agent revise existing rules instead of re-deriving from scratch.
    """
    if not rules:
        return "None yet — derive rules from scratch."

    def _sig(s: dict) -> str:
        name = (s.get("plain_name") or "").strip()
        return f"{s.get('type')}={s.get('value')}" + (f" (plain name: {name})" if name else "")

    lines = []
    for rule in rules.values():
        lines.append(f"- {rule.get('label_identifier')}:")
        for group in _include_groups(rule):
            if not group:
                continue
            joined = " AND ".join(_sig(s) for s in group)
            lines.append(f"    include group: {joined}")
        exclude = rule.get("exclude_signals") or []
        if exclude:
            lines.append("    exclude (any): " + ", ".join(_sig(s) for s in exclude))
        if rule.get("notes"):
            lines.append(f"    notes: {rule['notes']}")
    return "\n".join(lines)


def format_rules_block(rules: dict) -> str:
    """Render pending classification rules in plain language so non-technical group members
    can vet what each rule catches and what it leaves alone before approving."""
    lines = ["📋 **Proposed classification rules**", "─" * 15, "\n"]
    lines.append(
        "_These rules flag posts using specific words, text patterns, and account traits — "
        "they can't judge tone or meaning._"
    )

    for rule in rules.values():
        name = (rule.get("label_identifier") or "label").replace("_", " ").title()
        lines.append(f"\n• **{name}**")
        if rule.get("notes"):
            lines.append(f"  {rule['notes']}")

        groups = [g for g in _include_groups(rule) if g]
        if groups:
            lines.append("  Flags a post if ANY of these is true:")
            lines.extend(_condition_lines(groups))

        excludes = rule.get("exclude_signals") or []
        if excludes:
            lines.append(
                "\n  Never flags posts that also say: "
                + ", ".join(_signal_to_plain(s) for s in excludes)
            )

    lines += ["\nReact with 👍🏾 to approve these rules.", "─" * 40]
    return "\n".join(lines)


def format_labeler_context(labeler_config: dict) -> str:
    """Format labeler configuration into a readable string for prompt context."""
    if not labeler_config:
        return ""
    lines = []
    if labeler_config.get('display_name'):
        lines.append(f"Display name: {labeler_config['display_name']}")
    if labeler_config.get('description'):
        lines.append(f"Description: {labeler_config['description']}")
    if labeler_config.get('labels'):
        lines.append("Labels:")
        for label in labeler_config['labels']:
            locale_parts = []
            for loc in (label.get('locales') or []):
                loc_str = f"{loc.get('lang')}: {loc.get('name')}"
                if loc.get('description'):
                    loc_str += f" — {loc.get('description')}"
                locale_parts.append(loc_str)
            locales = ", ".join(locale_parts)
            lines.append(
                f"  - {label.get('identifier')}: severity={label.get('severity')}, "
                f"blurs={label.get('blurs')}"
                + (f", locales=[{locales}]" if locales else "")
            )
    return "\n".join(lines)


def format_config_block(labeler_config: dict) -> str:
    """Render the current labeler configuration as a fixed-format block shown directly to the user."""
    if not labeler_config:
        return "─" * 40 + "\n📋 No labeler configuration set.\n" + "─" * 40
    lines = ["─" * 40, "📋 Current labeler configuration\n"]
    if labeler_config.get("display_name"):
        lines.append(f"Display name: {labeler_config['display_name']}")
    if labeler_config.get("description"):
        lines.append(f"Description:  {labeler_config['description']}")
    if labeler_config.get("labels"):
        lines.append("Labels:")
        for label in labeler_config["labels"]:
            locale_parts = []
            for loc in (label.get("locales") or []):
                loc_str = f"{loc.get('lang')}: {loc.get('name')}"
                if loc.get('description'):
                    loc_str += f" — {loc.get('description')}"
                locale_parts.append(loc_str)
            locales = ", ".join(locale_parts)
            lines.append(
                f"  • {label.get('identifier')}: {_label_behavior(label)}"
                + (f" [{locales}]" if locales else "")
            )
    lines.append("─" * 40)
    return "\n".join(lines)