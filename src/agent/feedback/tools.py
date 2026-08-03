from typing import Literal, Annotated
from langchain_core.tools import tool
from langgraph.prebuilt import ToolNode, InjectedState
from pydantic import BaseModel, Field

from src.agent.feedback.state import FeedbackGraphState
from src.agent.feedback.signal_validation import sanitize_rules
from src.agent.state import LabelValueDefinition, LabelerDeclaration, ClassificationRule


class LocaleInput(BaseModel):
    lang: str = Field(description="Language code, e.g. 'en', 'fr'")
    name: str = Field(description="Label name in this language")
    description: str | None = Field(default=None, description="Label description in this language")


class LabelInput(BaseModel):
    identifier: str = Field(description="snake_case identifier derived from the label's purpose")
    severity: Literal['alert', 'inform', 'none'] = Field(default='inform', description="'alert' for harmful content, 'inform' for neutral/informational, 'none' for metadata")
    blurs: Literal['content', 'media', 'none'] = Field(default='none', description="'content' to hide the whole post behind a click-through, 'media' to hide images/video only, 'none' to hide nothing")
    locales: list[LocaleInput] = Field(default_factory=list, description="Label names and descriptions per language")


class SignalInput(BaseModel):
    type: Literal['keyword', 'pattern', 'account']
    value: str
    plain_name: str | None = Field(
        default=None,
        description=(
            "Short plain-language name for this signal, in the group's words — 'a cure word', "
            "'a sales pitch', 'brand-new accounts'. This is what the group sees on the approval "
            "card, so it must be readable by someone non-technical. Required for 'pattern' "
            "signals (a regex cannot be shown to the group); optional for keyword/account, "
            "which read plainly on their own."
        ),
    )

class GroupInput(BaseModel):
    all_of: list[SignalInput] = Field(
        description=(
            "Signals that must ALL match the same post for this group to fire. Use one signal "
            "when the thing is harmful on its own; use several when the group said it only "
            "counts in combination ('X plus Y', 'X when it's paired with Y')."
        )
    )

class RuleInput(BaseModel):
    label_identifier: str
    include_groups: list[GroupInput] = Field(
        description=(
            "The label applies if ANY group fires (groups are OR'd). Within a group, ALL "
            "signals must match the same post (AND)."
        )
    )
    exclude_signals: list[SignalInput] = Field(
        description="The label is skipped if ANY of these match the post."
    )
    notes: str | None


@tool
def get_label(
    identifier: str,
    state: Annotated[FeedbackGraphState, InjectedState]
) -> LabelValueDefinition | str:
    """Retrieves an existing label definition by identifier."""
    labels = state.get('labels') or {}
    label = labels.get(identifier)
    if label is None:
        available = ', '.join(labels.keys()) if labels else 'none'
        return f"Label '{identifier}' not found. Available labels: {available}"
    return label


@tool
def record_purpose(community: str, audience: str, goal: str) -> str:
    """Record what the group said the labeler is for, in their own words.

    Call this as soon as the group has described their community, who the labels are for, and what
    they want the labeler to accomplish — including when all three arrive in the same message as
    other details. This is what ends the purpose stage, so recording it is what lets the design
    move on. Do not invent or infer any of the three; if one is genuinely missing, ask for it.
    """
    return (
        "Purpose recorded. Now reply to the group in your own words: confirm what you understood "
        "in one or two sentences and tell them the next step is deciding what gets flagged."
    )


@tool
def note_for_later(details: list[str]) -> str:
    """Park design details the group raised before the stage that handles them.

    Use this when the group names things to flag, how posts should be treated, wording, or any
    other configuration specifics while the current stage is still establishing something else.
    Record each detail as its own short entry in the group's own words — these are replayed to you
    at the stage that acts on them, so the group never has to repeat themselves.
    """
    return (
        f"Noted {len(details)} detail(s) for the stage that handles them. Do not act on them now. "
        "Reply to the group in your own words: acknowledge what you noted in one short clause, "
        "then ask the question this stage exists to answer."
    )


@tool
def finalize_proposal(
    labels: list[LabelInput],
    display_name: str | None = None,
    description: str | None = None,
) -> str:
    """Stage a labeler configuration proposal for channel approval.
    Always include the complete set of labels for the final configuration — both unchanged
    labels and new or updated ones. Labels not included will be removed.
    """
    return "Proposal staged successfully."

@tool
def finalize_rules(rules: list[RuleInput]) -> str:
    """Stage classification rules derived from the labeler purpose
    for group approval. Rules are keyed by label identifier and
    include include groups / exclude signals with a human-readable rationale.

    Every signal must be enforceable by the labeler (keyword, valid regex pattern, or a
    valid account condition), every pattern signal must carry a plain-language name, and
    every label must keep at least one group with an enforceable signal. Unenforceable
    signals are reported back so they can be fixed or removed.
    """
    rule_dicts = [r.model_dump() if hasattr(r, "model_dump") else dict(r) for r in rules]
    cleaned_rules, errors = sanitize_rules(rule_dicts)

    if errors:
        return (
            "Some signals can't be enforced by this labeler and were rejected. Fix or remove "
            "them, then call finalize_rules again with the corrected rules:\n"
            + "\n".join(f"- {e}" for e in errors)
        )
    return f"Rules staged for approval ({len(cleaned_rules)} label(s))."


def commit_proposal(proposal: LabelerDeclaration, current_config: LabelerDeclaration) -> LabelerDeclaration:
    """Apply a proposed LabelerDeclaration to the current config.
    Called when a pending suggestion receives majority approval.
    """
    return {**(current_config or {}), **proposal}


def commit_rules(
    proposal: dict[str, ClassificationRule],
    current_rules: dict[str, ClassificationRule],
) -> dict[str, ClassificationRule]:
    """Apply proposed classification rules to the current rules, keyed by label identifier.
    Called when a pending rule suggestion receives majority approval.
    """
    return {**(current_rules or {}), **proposal}


tools = [get_label, record_purpose, note_for_later, finalize_proposal, finalize_rules]
tool_node = ToolNode(tools)
