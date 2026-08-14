"""
Majority voting logic for labeler config proposals and classification rule proposals.

When the feedback agent proposes a change, it is staged as a pending suggestion keyed by
the Stream message_id — pending_suggestions for labeler config/label proposals, or
pending_rule_suggestions for classification rules. Users approve by reacting with
APPROVAL_REACTION. Once the threshold is met, commit_proposal/commit_rules is called to
apply the change.
"""

import asyncio
import logging
from datetime import datetime, timezone

from langchain_core.messages import HumanMessage

from src.agent.brainstorming.graph import graph
from src.agent.brainstorming.nodes import _ANCHOR_SOURCES, _advance_setup_stage
from src.agent.feedback.tools import commit_proposal, commit_rules
from src.agent.lifecycle.provision import is_complete, merge_governance
from src.agent.spec import build_spec

logger = logging.getLogger(__name__)

APPROVAL_REACTION = "like"   # 👍🏾
MAJORITY_THRESHOLD = 2       # above this many voting members, a majority decides; at or below, everyone does


def _approval_marker(kind: str) -> HumanMessage:
    """The vote, written into the feedback agent's own conversation history.

    The agent decides what to do next from feedback_messages, not from labeler_config or
    setup_stage — and the last thing it finds itself saying is "staged for group approval,
    once approved...". Committing without recording the vote here leaves that claim
    uncontradicted, so the agent asks for an approval it already has, on a message whose
    suggestion is now committed and therefore inert (see the idempotency guard below) —
    a deadlock the group cannot react its way out of.
    """
    what = "labeler configuration" if kind == "proposal" else "classification rules"
    return HumanMessage(content=(
        f"[Group decision: the {what} you staged were put to a vote and APPROVED. They are "
        f"now committed and are the current {what} shown in your system prompt. This is done "
        f"— do not ask the group to approve them again, and do not re-stage them unchanged.]"
    ))


def _advance_setup_stage_fully(setup_stage: str | None, config: dict, classification_rules: dict) -> str | None:
    """Cascade _advance_setup_stage to the furthest stage the approved artifacts justify.

    _advance_setup_stage only moves one step per call. A single combined proposal
    (name + description + labels) approved while the stage is still 'purpose' must
    land on 'rules', not 'content' — so we loop until the stage stops moving.
    """
    stage = setup_stage
    while True:
        nxt = _advance_setup_stage(stage, config, classification_rules)
        if nxt == stage:
            return stage
        stage = nxt


def approvals_needed(voting_member_count: int) -> int:
    """How many approvals carry a vote: a majority above MAJORITY_THRESHOLD, everyone at or below.

    Small channels are UNANIMOUS, not single-approval. A majority of two is one, so the old rule
    let either member of a pair commit the group's design alone — and a channel reaches two the
    ordinary way, by someone not turning up. Nothing announced that: a session where one
    participant was absent ran with no vote at all while reading, in the transcript, exactly like
    one that had been agreed. Requiring both makes a channel too small to have a majority say so
    by stalling, which is visible, instead of by deciding, which is not.

    The counterpart of _threshold_met — same rule, stated as a number so CLEO can tell the group
    what a pending vote is still waiting on.
    """
    if voting_member_count > MAJORITY_THRESHOLD:
        return voting_member_count // 2 + 1
    # An empty or unknown roster still has to need SOMETHING: a threshold of 0 is met by the vote
    # that hasn't happened yet, so every card would commit itself the moment it was staged.
    return max(voting_member_count, 1)


def _threshold_met(approved_by: list, voting_member_count: int) -> bool:
    """Majority of the voting members, or all of them in channels too small to have a majority."""
    return len(approved_by) >= approvals_needed(voting_member_count)


def anchor_vote_progress(state_values: dict, message_id: str) -> tuple[str, int] | None:
    """(anchor kind, approvals recorded so far) for the still-live card `message_id` is, else None.

    None covers every "nothing to report" case at once: the message is not an anchor (a 👍🏾 on
    ordinary chat), or it is one that has already been committed or superseded and whose own
    handler has said so. Reads the same _ANCHOR_SOURCES the pending-vote reporting does, so a
    tally quoted here can't disagree with the one CLEO gives when asked.

    Exists because every process_* gate returns the same falsy value whether it ignored a reaction
    or recorded it and is still waiting — a distinction the group needs and the return type can't
    make (see reactions._report_vote_progress).
    """
    for kind, key, shape in _ANCHOR_SOURCES:
        store = state_values.get(key)
        if not store:
            continue
        if shape == "one":
            entry = store if store.get("message_id") == message_id else None
        else:
            entry = store.get(message_id)
        if not entry or entry.get("committed") or entry.get("superseded"):
            continue
        return kind, len(entry.get("approved_by") or [])
    return None


def superseded_entries(suggestions: dict | None, keep_message_id: str) -> dict:
    """Mark every still-live suggestion other than `keep_message_id` as superseded.

    Returned as a merge-able fragment for pending_suggestions / pending_rule_suggestions (both
    merge dicts on update). Callers stage it alongside the new suggestion so exactly one anchor
    is ever votable: the group's scroll keeps every card CLEO posted, and reactions resolve by
    message_id alone, so without this a 👍🏾 on an older card commits a superseded design.
    """
    return {
        message_id: {**suggestion, "superseded": True}
        for message_id, suggestion in (suggestions or {}).items()
        if message_id != keep_message_id
        and not suggestion.get("committed")
        and not suggestion.get("superseded")
    }


async def is_superseded(channel_id: str, message_id: str) -> bool:
    """True if this message held a proposal that a later one replaced.

    Lets the reaction handler explain the silence — a vote on a superseded card does nothing, and
    "nothing happened" is indistinguishable from a bug to the person who reacted.
    """
    state = await asyncio.to_thread(graph.get_state, {"configurable": {"thread_id": channel_id}})
    for key in ("pending_suggestions", "pending_rule_suggestions"):
        suggestion = (state.values.get(key) or {}).get(message_id)
        if suggestion and suggestion.get("superseded"):
            return True
    return False


def _stamp_spec_id(update: dict, labeler_config: dict, classification_rules: dict, context: str) -> None:
    """Record the content hash of the current approved design into `update`.

    Keeps spec_id in step with labeler_config + classification_rules so the preview renders the
    right thing. An unchanged design yields an unchanged spec_id; a real edit yields a new one.
    """
    spec = build_spec(labeler_config, classification_rules)
    update["spec_id"] = spec["spec_id"]
    if spec["warnings"]:
        logger.info("Spec built (%s) with warnings: %s", context, spec["warnings"])


async def process_approval_vote(
    channel_id: str,
    message_id: str,
    reactor_user_id: str,
    voting_member_count: int,) -> str | None:
    """Record an approval vote and apply the proposal if the threshold is met.

    Returns "proposal" or "rules" indicating what kind of suggestion was applied,
    or None if the message has no pending suggestion or the threshold wasn't met.
    """
    config = {"configurable": {"thread_id": channel_id}}

    state = await asyncio.to_thread(graph.get_state, config)
    pending_suggestions = state.values.get("pending_suggestions", {})
    pending_rule_suggestions = state.values.get("pending_rule_suggestions", {})

    if message_id in pending_suggestions:
        kind = "proposal"
        state_key = "pending_suggestions"
        suggestion = pending_suggestions[message_id]
    elif message_id in pending_rule_suggestions:
        kind = "rules"
        state_key = "pending_rule_suggestions"
        suggestion = pending_rule_suggestions[message_id]
    else:
        return None

    # Idempotency: once a suggestion has been committed, further approval reactions
    # on the same message are not not re-committing or re-announcing.
    if suggestion.get("committed"):
        return None

    # A newer proposal replaced this one, so approving it would commit a design the group has
    # already moved past. The reaction handler says so rather than leaving it silent.
    if suggestion.get("superseded"):
        logger.info("Ignoring approval on superseded suggestion %s in %s", message_id, channel_id)
        return None

    approved_by = list(set(suggestion.get("approved_by", []) + [reactor_user_id]))
    await asyncio.to_thread(
        graph.update_state, config,
        {state_key: {message_id: {**suggestion, "approved_by": approved_by}}},
    )

    threshold_met = _threshold_met(approved_by, voting_member_count)

    logger.info(
        "Vote on message %s (%s): %d/%d approvals (threshold_met=%s)",
        message_id, kind, len(approved_by), voting_member_count, threshold_met,
    )

    if not threshold_met:
        return None

    setup_stage = state.values.get("setup_stage")

    if kind == "proposal":
        current_config = state.values.get("labeler_config") or {}
        new_config = commit_proposal(suggestion["proposal"], current_config)
        new_stage = _advance_setup_stage_fully(
            setup_stage, new_config, state.values.get("classification_rules") or {}
        )

        proposal_update = {
            "labeler_config": new_config,
            "setup_stage": new_stage,
            "feedback_messages": [_approval_marker(kind)],
        }

        # Labels are approved, so the details parked while CLEO was establishing purpose have been
        # acted on. Clearing them keeps spent asks from trailing the group into every later turn.
        if new_stage in ('rules', 'complete'):
            proposal_update["design_notes"] = None

        # A label/config edit while the lifecycle is already active (preview iteration) 
        # changes the design, so refresh spec_id.
        if state.values.get("lifecycle_stage") is not None:
            _stamp_spec_id(
                proposal_update, new_config, state.values.get("classification_rules") or {}, "config commit"
            )

        await asyncio.to_thread(graph.update_state, config, proposal_update)

        logger.info(
            "Labeler config updated after majority approval on message %s (setup_stage: %s -> %s)",
            message_id, setup_stage, new_stage,
        )
    else:
        current_rules = state.values.get("classification_rules") or {}
        new_rules = commit_rules(suggestion["proposal"], current_rules)
        new_stage = _advance_setup_stage_fully(
            setup_stage, state.values.get("labeler_config") or {}, new_rules
        )

        rules_update = {
            "classification_rules": new_rules,
            "setup_stage": new_stage,
            "feedback_messages": [_approval_marker(kind)],
        }

        lifecycle_stage = state.values.get("lifecycle_stage")

        # Handoff into the post-setup lifecycle: the first time a rules approval completes
        # setup, open the preview stage.
        if new_stage == "complete" and lifecycle_stage is None:
            rules_update["lifecycle_stage"] = "preview"
            rules_update["lifecycle_status"] = "pending"

        # Keep spec_id in step with the approved design — stamped at the handoff, and refreshed
        # on any later rules edit while the lifecycle is active (preview iteration / maintenance).
        if rules_update.get("lifecycle_stage") is not None or lifecycle_stage is not None:
            _stamp_spec_id(
                rules_update, state.values.get("labeler_config") or {}, new_rules, "rules commit"
            )

        await asyncio.to_thread(graph.update_state, config, rules_update)

        logger.info(
            "Classification rules updated after majority approval on message %s "
            "(setup_stage: %s -> %s, lifecycle_stage: %s)",
            message_id, setup_stage, new_stage, rules_update.get("lifecycle_stage", "unchanged"),
        )

    # Mark the suggestion committed so subsequent approval reactions are no-ops.
    await asyncio.to_thread(
        graph.update_state, config,
        {state_key: {message_id: {**suggestion, "approved_by": approved_by, "committed": True}}},
    )

    return kind


async def process_preview_approval(
    channel_id: str,
    message_id: str,
    reactor_user_id: str,
    voting_member_count: int,
) -> bool:
    """Record an approval reaction on the preview-approval message; advance to bundle generation if met.

    Returns True only when this reaction pushed the vote over the threshold and moved the lifecycle
    preview -> generate (materialize the sandbox bundle). Returns False if the message isn't the
    pending preview-approval anchor, the channel isn't in preview, the advance already fired, or the
    threshold isn't met yet. The irreversible provision step runs later, only after sandbox testing.
    """
    config = {"configurable": {"thread_id": channel_id}}
    state = await asyncio.to_thread(graph.get_state, config)

    pending = state.values.get("pending_preview_approval")
    if not pending or pending.get("message_id") != message_id or pending.get("committed"):
        return False
    if state.values.get("lifecycle_stage") != "preview":
        return False

    approved_by = list(set(pending.get("approved_by", []) + [reactor_user_id]))

    if not _threshold_met(approved_by, voting_member_count):
        await asyncio.to_thread(
            graph.update_state, config,
            {"pending_preview_approval": {**pending, "approved_by": approved_by}},
        )
        logger.info(
            "Preview-approval vote on %s: %d/%d approvals (not yet met)",
            message_id, len(approved_by), voting_member_count,
        )
        return False

    await asyncio.to_thread(
        graph.update_state, config,
        {
            "lifecycle_stage": "generate",
            "lifecycle_status": "pending",
            "pending_preview_approval": {**pending, "approved_by": approved_by, "committed": True},
        },
    )
    logger.info("Preview approved on message %s — lifecycle preview -> generate", message_id)
    return True


async def process_deploy_approval(
    channel_id: str,
    message_id: str,
    reactor_user_id: str,
    voting_member_count: int,
) -> bool:
    """Record a reaction on the ship-gate anchor; advance generate -> deploy if the threshold is met.

    The ship gate is the single explicit "go" on the (informational) rule-quality report: reacting to
    the anchor message advances the lifecycle so the sandbox bundle is materialized. Returns True only
    when this reaction pushed the vote over the threshold and moved generate -> deploy. Returns False
    if the message isn't the pending deploy-approval anchor, the channel isn't in generate, the
    advance already fired, or the threshold isn't met yet. Mirrors process_preview_approval.
    """
    config = {"configurable": {"thread_id": channel_id}}
    state = await asyncio.to_thread(graph.get_state, config)

    pending = state.values.get("pending_deploy_approval")
    if not pending or pending.get("message_id") != message_id or pending.get("committed"):
        return False
    if state.values.get("lifecycle_stage") != "generate":
        return False

    approved_by = list(set(pending.get("approved_by", []) + [reactor_user_id]))

    if not _threshold_met(approved_by, voting_member_count):
        await asyncio.to_thread(
            graph.update_state, config,
            {"pending_deploy_approval": {**pending, "approved_by": approved_by}},
        )
        logger.info(
            "Deploy-approval vote on %s: %d/%d approvals (not yet met)",
            message_id, len(approved_by), voting_member_count,
        )
        return False

    await asyncio.to_thread(
        graph.update_state, config,
        {
            "lifecycle_stage": "deploy",
            "lifecycle_status": "pending",
            "pending_deploy_approval": {**pending, "approved_by": approved_by, "committed": True},
        },
    )
    logger.info("Ship gate approved on message %s — lifecycle generate -> deploy", message_id)
    return True


# The two halves of the fork posted after the sandbox run. Exactly one is taken; taking either
# closes the other (see close_other_path).
_PATH_KEYS = {"provision": "pending_provision_approval", "guide": "pending_guide_choice"}


async def process_guide_choice(
    channel_id: str,
    message_id: str,
    reactor_user_id: str,
    voting_member_count: int,
) -> bool:
    """Record a reaction on the maintenance-guide anchor; report whether the group has chosen it.

    The only gate that advances NOTHING: choosing the guide means staying in the private sandbox,
    so the channel remains at `deploy` and the labeler keeps running exactly as it was. All this
    does is settle which of the two paths the group took, so the other can be closed and the guide
    posted.

    Guarded to `deploy` for the same reason as its twin: the anchor sits in the scroll forever, and
    a reaction on it after the group has moved on must not re-answer a question they've passed.
    """
    config = {"configurable": {"thread_id": channel_id}}
    state = await asyncio.to_thread(graph.get_state, config)

    pending = state.values.get("pending_guide_choice")
    if not pending or pending.get("message_id") != message_id or pending.get("committed"):
        return False
    if state.values.get("lifecycle_stage") != "deploy":
        return False

    approved_by = list(set(pending.get("approved_by", []) + [reactor_user_id]))

    if not _threshold_met(approved_by, voting_member_count):
        await asyncio.to_thread(
            graph.update_state, config,
            {"pending_guide_choice": {**pending, "approved_by": approved_by}},
        )
        logger.info(
            "Guide-path vote on %s: %d/%d approvals (not yet met)",
            message_id, len(approved_by), voting_member_count,
        )
        return False

    await asyncio.to_thread(
        graph.update_state, config,
        {"pending_guide_choice": {**pending, "approved_by": approved_by, "committed": True}},
    )
    logger.info("Guide path chosen on message %s — staying at deploy", message_id)
    return True


async def close_other_path(channel_id: str, chosen: str) -> str | None:
    """Close the fork half the group didn't take; return its anchor id so the caller can retag it.

    Marking it committed is what makes a later reaction on it inert — without this, a group that
    picked the guide could still trip into the going-live questions weeks later by reacting to a
    card that was never withdrawn. Returns None when there is nothing to close (the losing anchor
    was never registered, or is already closed), so the caller can skip the Stream write.
    """
    other_key = _PATH_KEYS["guide" if chosen == "provision" else "provision"]

    config = {"configurable": {"thread_id": channel_id}}
    state = await asyncio.to_thread(graph.get_state, config)
    pending = state.values.get(other_key)

    if not pending or pending.get("committed"):
        return None

    await asyncio.to_thread(
        graph.update_state, config, {other_key: {**pending, "committed": True}},
    )
    logger.info("Closed the %s path for %s (group chose %s)", other_key, channel_id, chosen)
    return pending.get("message_id")


async def process_provision_approval(
    channel_id: str,
    message_id: str,
    reactor_user_id: str,
    voting_member_count: int,
) -> bool:
    """Record a reaction on the go-live anchor; advance deploy -> provision if the threshold is met.

    The gate that opens the governance conversation. Mirrors process_deploy_approval. NOTE this is
    not the irreversible step every earlier message warned about — provision collects the group's
    decisions (handle, custodian, appeals contact) and mints nothing; see lifecycle/provision.py.
    """
    config = {"configurable": {"thread_id": channel_id}}
    state = await asyncio.to_thread(graph.get_state, config)

    pending = state.values.get("pending_provision_approval")
    if not pending or pending.get("message_id") != message_id or pending.get("committed"):
        return False
    if state.values.get("lifecycle_stage") != "deploy":
        return False

    approved_by = list(set(pending.get("approved_by", []) + [reactor_user_id]))

    if not _threshold_met(approved_by, voting_member_count):
        await asyncio.to_thread(
            graph.update_state, config,
            {"pending_provision_approval": {**pending, "approved_by": approved_by}},
        )
        logger.info(
            "Go-live vote on %s: %d/%d approvals (not yet met)",
            message_id, len(approved_by), voting_member_count,
        )
        return False

    await asyncio.to_thread(
        graph.update_state, config,
        {
            "lifecycle_stage": "provision",
            "lifecycle_status": "pending",
            "pending_provision_approval": {**pending, "approved_by": approved_by, "committed": True},
        },
    )
    logger.info("Go-live approved on message %s — lifecycle deploy -> provision", message_id)
    return True


async def process_governance_approval(
    channel_id: str,
    message_id: str,
    reactor_user_id: str,
    voting_member_count: int,
) -> dict | None:
    """Record a reaction on a governance confirm card; commit the answers if the threshold is met.

    Returns the merged GovernanceRecord when this reaction committed it, else None. Unlike the
    stage gates above this commits an ARTIFACT (the group's decisions), so it mirrors
    process_approval_vote's shape: merge via provision.merge_governance, mark the suggestion
    committed so a late reaction can't re-apply it, and leave the stage at `provision` — completing
    the answers is not the same as deploying, which isn't built.
    """
    config = {"configurable": {"thread_id": channel_id}}
    state = await asyncio.to_thread(graph.get_state, config)

    suggestions = state.values.get("pending_governance_suggestions") or {}
    pending = suggestions.get(message_id)
    if not pending or pending.get("committed"):
        return None
    if state.values.get("lifecycle_stage") != "provision":
        return None

    approved_by = list(set(pending.get("approved_by", []) + [reactor_user_id]))

    if not _threshold_met(approved_by, voting_member_count):
        await asyncio.to_thread(
            graph.update_state, config,
            {"pending_governance_suggestions": {message_id: {**pending, "approved_by": approved_by}}},
        )
        logger.info(
            "Governance vote on %s: %d/%d approvals (not yet met)",
            message_id, len(approved_by), voting_member_count,
        )
        return None

    merged = merge_governance(
        state.values.get("governance") or {},
        pending.get("proposal") or {},
        datetime.now(timezone.utc).isoformat(),
    )
    await asyncio.to_thread(
        graph.update_state, config,
        {
            "governance": merged,
            "pending_governance_suggestions": {
                message_id: {**pending, "approved_by": approved_by, "committed": True}
            },
            "lifecycle_status": "succeeded" if is_complete(merged) else "in_progress",
            "lifecycle_error": None,
        },
    )
    logger.info("Governance answers committed for %s on message %s", channel_id, message_id)
    return merged
