"""
Approval-reaction handling: turning a 👍🏾 into a tallied vote and, once the threshold is met, the
stage handoff that follows.

This is the seam where the group's votes drive the state machine — proposal/rules approvals during
setup, then the preview -> generate and generate -> deploy gates. Each gate checks its own pending
anchor and lifecycle stage, so they're mutually exclusive and can be tried in order.
"""

import asyncio
import collections
import logging

from src.agent.brainstorming.graph import graph
from src.agent.brainstorming.voting import (
    anchor_vote_progress,
    approvals_needed,
    close_other_path,
    is_superseded,
    process_approval_vote,
    process_deploy_approval,
    process_governance_approval,
    process_guide_choice,
    process_preview_approval,
    process_provision_approval,
)
from src.agent.lifecycle.provision import is_complete, outstanding_keys
from src.api.messages import (
    BUNDLE_STAGE_INTRO,
    GO_LIVE_PATH_CLOSED_NOTE,
    GOVERNANCE_COMPLETE_MSG,
    GUIDE_CHOSEN_MSG,
    GUIDE_PATH_CLOSED_NOTE,
    PREVIEW_APPROVAL_PROMPT,
    RULES_STAGE_INTRO,
    SANDBOX_STAGE_INTRO,
    SUPERSEDED_VOTE_MSG,
    VOTE_PROGRESS_MSG,
    preview_stage_intro,
)
from src.api.reporters import (
    _format_answers,
    _format_outstanding,
    _run_deploy_and_report,
    _run_generate_and_report,
    _run_provision_and_report,
    close_path_in_stream,
)
from src.api.stream import (
    AI_USER_ID,
    APPROVAL_APPROVED,
    approval_anchor,
    get_stream_client,
    is_voting_member,
    set_approval_state,
)
from src.config import FRONTEND_URL

logger = logging.getLogger(__name__)

# One lock per channel serializing approval-vote processing. Separate from the agent's channel locks
# so a vote never blocks on a multi-second agent run — it only guards the short read-modify-write of
# the vote tally (approved_by), which otherwise races when two members react at nearly the same time:
# both read the same approved_by, each writes back its own voter, and the second write clobbers the first.
_vote_locks: dict[str, asyncio.Lock] = collections.defaultdict(asyncio.Lock)


async def _report_vote_progress(
    channel, channel_id: str, message_id: str, voting_count: int
) -> None:
    """Say where a counted-but-sub-threshold vote stands.

    The last thing tried, after every gate has declined the reaction. Two very different outcomes
    arrive here identically: a 👍🏾 on ordinary chat (nothing to say) and a 👍🏾 that was recorded on
    a live card still short of the threshold. Only the second gets a reply, and it has to come
    from somewhere — set_approval_state only ever fires on APPROVED, and the card carries no count,
    so the group's first vote of two otherwise changes nothing they can see.
    """
    state = await asyncio.to_thread(graph.get_state, {"configurable": {"thread_id": channel_id}})
    progress = anchor_vote_progress(state.values, message_id)
    if progress is None:
        return

    kind, approved = progress
    needed = approvals_needed(voting_count)
    if approved >= needed:
        # Carried on this very reaction, and some gate above already announced it. Reporting a
        # count now would follow that confirmation with a card that still sounds unfinished.
        return

    logger.info(
        "Sub-threshold vote on %s (%s): %d/%d in %s", message_id, kind, approved, needed, channel_id
    )
    await asyncio.to_thread(
        channel.send_message,
        {"text": VOTE_PROGRESS_MSG.format(
            approved=approved, needed=needed, remaining=needed - approved
        )},
        AI_USER_ID,
    )


async def _process_approval_reaction(
    channel_type: str, channel_id: str, message_id: str, reactor_user_id: str
) -> None:
    """Tally an approval (👍🏾) reaction and, if the threshold is met, commit the change and hand off
    to the next stage. The caller serializes this per channel (see _vote_locks) so the read-modify-
    write of the vote tally in process_* can't interleave with another concurrent vote."""
    # Fetch the voting member count then delegate to voting logic
    client = get_stream_client()
    channel = client.channel(channel_type, channel_id)
    result = await asyncio.to_thread(channel.query)
    members = result.get("members", [])
    voting_count = len([m for m in members if is_voting_member(m["user_id"])])

    # Capture the setup stage before the vote so we can tell whether this approval
    # advanced the group into a new stage (e.g. content -> rules).
    graph_config = {"configurable": {"thread_id": channel_id}}
    pre_state = await asyncio.to_thread(graph.get_state, graph_config)
    pre_stage = pre_state.values.get("setup_stage")
    pre_lifecycle = pre_state.values.get("lifecycle_stage")

    approved_kind = await process_approval_vote(channel_id, message_id, reactor_user_id, voting_count)

    if approved_kind:
        # The card the group just carried. Retagged before anything else so the bubble settles at
        # the same time as the confirmation that follows it.
        await set_approval_state(message_id, APPROVAL_APPROVED)

        state = await asyncio.to_thread(graph.get_state, graph_config)
        post_stage = state.values.get("setup_stage")
        # True only on the actual handoff into preview (lifecycle None -> preview), so the
        # preview link isn't re-announced on later rule edits during preview or maintenance.
        entered_preview = pre_lifecycle is None and state.values.get("lifecycle_stage") == "preview"

        if approved_kind == "rules":
            rule_count = len(state.values.get("classification_rules") or {})
            rule_word = "rule" if rule_count == 1 else "rules"
            confirmation = f"✅ Rules approved! {rule_count} classification {rule_word} now active."
            if entered_preview:
                confirmation += "\n\n" + preview_stage_intro(channel_id)
        else:
            new_config = state.values.get("labeler_config") or {}
            display_name = new_config.get("display_name") or "labeler"
            label_count = len(new_config.get("labels") or [])
            label_word = "label" if label_count == 1 else "labels"
            confirmation = (
                f"✅ Proposal approved! *{display_name}* has been updated "
                f"({label_count} {label_word} configured)."
            )

        await asyncio.to_thread(channel.send_message, {"text": confirmation}, AI_USER_ID)

        # Proactively hand off to the next stage when this approval just moved the
        # group into 'rules', so they don't have to ask what comes next.
        if approved_kind == "proposal" and pre_stage != "rules" and post_stage == "rules":
            await asyncio.to_thread(channel.send_message, {"text": RULES_STAGE_INTRO}, AI_USER_ID)

        # On the preview handoff, post the approval anchor and register the pending vote so
        # reactions to it advance preview -> generate (materialize + sandbox-test).
        if entered_preview:
            resp = await asyncio.to_thread(
                channel.send_message, approval_anchor(PREVIEW_APPROVAL_PROMPT), AI_USER_ID
            )
            msg_obj = resp.get("message", {}) if isinstance(resp, dict) else {}
            approval_msg_id = msg_obj.get("id") if isinstance(msg_obj, dict) else getattr(msg_obj, "id", None)
            if approval_msg_id:
                await asyncio.to_thread(
                    graph.update_state, graph_config,
                    {"pending_preview_approval": {"message_id": approval_msg_id, "approved_by": []}},
                )

        # If the design was edited AFTER the quality check already ran (lifecycle == 'generate'),
        # re-run it so the report reflects the new rules — the "tell me and we'll re-check" promise.
        # run_generate_stage reuses the cached corpus (keyed by domain, not spec_id) and just
        # re-evaluates + re-posts the report and a fresh ship-gate anchor. Guarded to 'generate' so
        # preview-stage edits (which only refresh the client-side preview) and the preview handoff
        # (post-lifecycle == 'preview') don't trigger it.
        if state.values.get("lifecycle_stage") == "generate":
            asyncio.create_task(_run_generate_and_report(channel_type, channel_id))
    elif await is_superseded(channel_id, message_id):
        # A vote on a proposal a later one replaced. process_approval_vote already ignored it;
        # say why, or the reaction looks like a button that silently does nothing.
        await asyncio.to_thread(channel.send_message, {"text": SUPERSEDED_VOTE_MSG}, AI_USER_ID)
    else:
        # Not a proposal/rules vote — it may be a preview approval (preview -> generate) or a
        # ship approval on the quality report (generate -> deploy). Each gate checks its own
        # pending anchor + lifecycle stage, so they're mutually exclusive; try them in order.
        # Whichever one fires, message_id IS that gate's anchor, so it's the bubble to retag.
        advanced = await process_preview_approval(channel_id, message_id, reactor_user_id, voting_count)
        if advanced:
            await set_approval_state(message_id, APPROVAL_APPROVED)
            await asyncio.to_thread(channel.send_message, {"text": SANDBOX_STAGE_INTRO}, AI_USER_ID)
            # Kick off the generate stage (corpus sourcing) in the background so the webhook
            # returns promptly; it reports the outcome back into the channel when done.
            asyncio.create_task(_run_generate_and_report(channel_type, channel_id))
        elif await process_deploy_approval(channel_id, message_id, reactor_user_id, voting_count):
            await set_approval_state(message_id, APPROVAL_APPROVED)
            await asyncio.to_thread(channel.send_message, {"text": BUNDLE_STAGE_INTRO}, AI_USER_ID)
            # Materialize the sandbox bundle in the background; reports the outcome when done.
            asyncio.create_task(_run_deploy_and_report(channel_type, channel_id))
        elif await process_provision_approval(channel_id, message_id, reactor_user_id, voting_count):
            # Go-live gate: opens the governance questions. Nothing is created by advancing here.
            await set_approval_state(message_id, APPROVAL_APPROVED)
            # The fork is decided — the guide half must stop being reactable, or a later 👍🏾 on it
            # would answer a question the group has already moved past.
            await close_path_in_stream(
                await close_other_path(channel_id, "provision"),
                GUIDE_PATH_CLOSED_NOTE.format(guide_url=f"{FRONTEND_URL}/?guide={channel_id}"),
            )
            asyncio.create_task(_run_provision_and_report(channel_type, channel_id))
        elif await process_guide_choice(channel_id, message_id, reactor_user_id, voting_count):
            # The other half of the same fork. Advances nothing: the labeler stays in the sandbox
            # and the channel stays at `deploy`, so this posts the guide and closes going-live.
            await set_approval_state(message_id, APPROVAL_APPROVED)
            await close_path_in_stream(
                await close_other_path(channel_id, "guide"), GO_LIVE_PATH_CLOSED_NOTE
            )
            await asyncio.to_thread(
                channel.send_message,
                {"text": GUIDE_CHOSEN_MSG.format(guide_url=f"{FRONTEND_URL}/?guide={channel_id}")},
                AI_USER_ID,
            )
        else:
            # Last gate: a confirm card on the governance answers. Unlike the stage gates this
            # commits an artifact, so it reports what was recorded (and, once all three are in,
            # says plainly that this is as far as the system goes).
            merged = await process_governance_approval(
                channel_id, message_id, reactor_user_id, voting_count
            )
            if merged is not None:
                await set_approval_state(message_id, APPROVAL_APPROVED)
                if is_complete(merged):
                    text = GOVERNANCE_COMPLETE_MSG.format(
                        answers=_format_answers(merged),
                        guide_url=f"{FRONTEND_URL}/?guide={channel_id}",
                    )
                else:
                    remaining = _format_outstanding(outstanding_keys(merged))
                    text = (
                        f"✅ Recorded.\n\n{_format_answers(merged)}"
                        f"\n\nStill to settle: {remaining}."
                    )
                await asyncio.to_thread(channel.send_message, {"text": text}, AI_USER_ID)
            else:
                # No gate claimed the reaction. Either it wasn't on a votable card, or one of them
                # counted it and is still waiting — only the tally can tell the two apart.
                await _report_vote_progress(channel, channel_id, message_id, voting_count)
