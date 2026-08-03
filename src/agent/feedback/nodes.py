import logging

from langchain_core.messages import HumanMessage
from src.config import tool_model

from src.agent.feedback.tools import tools
from src.agent.feedback.state import FeedbackGraphState
from src.agent.prompts import FEEDBACK_AGENT_PROMPT, LABEL_BEHAVIOR_TABLE

logger = logging.getLogger(__name__)

# tool_model, not the shared `model`: this agent emits finalize_rules calls far larger than
# the shared 1,000-token budget allows, and truncation here is silent (see src/config.py).
tools_model = tool_model.bind_tools(tools)


def _last_user_query(messages: list) -> str:
    for m in reversed(messages):
        if isinstance(m, HumanMessage) or getattr(m, "type", None) == "human":
            return m.content
    return messages[0].content if messages else ""


def _format_purpose(purpose: dict | None) -> str:
    """Render the recorded purpose, or say plainly that it's still the open question."""
    if not purpose:
        return "Not established yet — this is what the 'purpose' stage is for."
    lines = [
        f"Community: {purpose['community']}" if purpose.get('community') else None,
        f"Audience: {purpose['audience']}" if purpose.get('audience') else None,
        f"Goal: {purpose['goal']}" if purpose.get('goal') else None,
    ]
    return "\n".join(line for line in lines if line) or "Not established yet."


def dedupe_notes(notes: list[str] | None) -> list[str]:
    """Parked details in first-mention order, without repeats.

    design_notes is additive, so a detail the group restates (or the agent re-notes on a later
    turn) lands twice. Public because the stage that consumes the notes shows them to the group.
    """
    seen, ordered = set(), []
    for note in notes or []:
        key = note.strip().lower()
        if key and key not in seen:
            seen.add(key)
            ordered.append(note.strip())
    return ordered


def _format_notes(notes: list[str] | None) -> str:
    deduped = dedupe_notes(notes)
    if not deduped:
        return "Nothing parked yet."
    return "\n".join(f"- {note}" for note in deduped)


def _format_current_config(config: dict, labels: dict) -> str:
    if not config and not labels:
        return "No configuration yet — this is a new labeler."
    lines = []
    if config.get('display_name'):
        lines.append(f"Display name: {config['display_name']}")
    if config.get('description'):
        lines.append(f"Description: {config['description']}")
    if labels:
        lines.append("Labels:")
        for label in labels.values():
            lines.append(
                f"  - {label.get('identifier')}: severity={label.get('severity')}, "
                f"blurs={label.get('blurs')}"
            )
    return "\n".join(lines) if lines else "No configuration yet — this is a new labeler."


def call_model(state: FeedbackGraphState):
    messages = state['messages']
    query = _last_user_query(messages)
    current_config = _format_current_config(
        state.get('labeler_config') or {},
        state.get('labels') or {}
    )
    # Measured: "what do we need to do to move forward?" classifies as feedback, not question, so
    # this agent — not draft_response — is what answers it. It needs the same live stage context.
    stage_context = (state.get('stage_context') or "").strip() or "Not established yet."

    if state.get('system_prompt'):
        # Rules derivation supplies its own prompt; append rather than lose the stage context.
        prompt = (
            f"{state['system_prompt']}\n\n"
            f"Where the group is right now — the live state of this channel:\n{stage_context}\n"
            "If they ask where they are or what happens next, answer from this and nothing else."
        )
    else:
        prompt = FEEDBACK_AGENT_PROMPT.format(
            query=query,
            current_config=current_config,
            current_purpose=_format_purpose(state.get('community_purpose')),
            noted_details=_format_notes(state.get('design_notes')),
            behavior_table=LABEL_BEHAVIOR_TABLE,
            stage_context=stage_context,
            setup_stage=state.get('setup_stage', ''))
    messages = [{'role': 'system', 'content': prompt}] + messages

    response = tools_model.invoke(messages)

    # A response cut off at max_tokens loses the tail of its tool-call JSON, and the partial
    # parse yields a finalize_* call with its rules/labels silently dropped. Raise at failure
    # instead of staging an empty proposal the group would then approve.
    if getattr(response, 'response_metadata', {}).get('stop_reason') == 'max_tokens':
        logger.error(
            "Feedback model hit max_tokens (%s) — tool-call args are truncated. "
            "Raise TOOL_MODEL_MAX_TOKENS in src/config.py.",
            getattr(response, 'usage_metadata', {}).get('output_tokens'),
        )
        raise RuntimeError(
            "The model's response was cut off before it finished. This usually means there "
            "was too much to write at once — try splitting the request into smaller steps."
        )

    return {"messages": [response]}