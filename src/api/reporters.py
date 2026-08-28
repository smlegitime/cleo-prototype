"""
Background tasks that run a lifecycle stage and report the outcome back into the channel.

Each one is fire-and-forget (`asyncio.create_task`) and recreates its own channel handle, so the
webhook that triggered it returns promptly instead of blocking on a multi-second fetch, bundle
materialization, or sandbox run. They own the *chat-facing* half of a stage: the heavy work itself
lives in src/agent/lifecycle/, and the copy they post lives in messages.py.
"""

import asyncio
import logging

from langchain_core.messages import HumanMessage, SystemMessage

from src.agent.brainstorming.graph import graph
from src.agent.brainstorming.voting import approvals_needed
from src.agent.lifecycle import (
    capture_governance,
    run_deploy_stage,
    run_execute_stage,
    run_generate_stage,
    run_provision_stage,
    stand_down_provision,
)
from src.agent.lifecycle.quality import format_report_summary
from src.agent.maintenance_guide import answered_fields
from src.agent.prompts import WELCOME_MESSAGE_PROMPT
from src.api.messages import (
    BUNDLE_FAILED_MSG,
    BUNDLE_READY_MSG,
    CORPUS_FAILED_MSG,
    CORPUS_SOURCED_MSG,
    DEPLOY_APPROVAL_PROMPT,
    DEPLOY_GATE_SUPERSEDED_NOTE,
    GO_LIVE_PATH_PROMPT,
    GO_LIVE_REOPENED_MSG,
    GOVERNANCE_CONFIRM_CARD,
    GOVERNANCE_REMAINING_MSG,
    GUIDE_PATH_PROMPT,
    PATH_CHOICE_INTRO,
    PROVISION_KEPT_ANSWERS_MSG,
    PROVISION_STOOD_DOWN_MSG,
    SANDBOX_RUN_FAILED_MSG,
    SANDBOX_RUN_REPORT,
    THRESHOLD_CHANGED_MSG,
    UNEXPECTED_ERROR_MSG,
    provision_stage_intro,
)
from src.api.stream import (
    AI_USER_ID,
    AI_USER_NAME,
    APPROVAL_SUPERSEDED,
    _set_ai_indicator,
    _update_stream_message,
    approval_anchor,
    get_stream_client,
    is_voting_member,
    set_approval_state,
)
from src.config import fast_model, FRONTEND_URL

logger = logging.getLogger(__name__)


async def _report_unexpected(channel_type: str, channel_id: str) -> None:
    """Post the generic failure notice into a channel after an UNHANDLED error in a background task.

    Expected failures (no corpus, bundle won't build, sandbox won't start) have their own specific
    copy. This covers everything else, which used to be logged and swallowed — leaving the group
    looking at a channel that had simply stopped talking, with no way to tell "thinking" from
    "dead". A visible, timestamped marker in the transcript is worth more than clean silence.

    Best-effort and never raises: if Stream itself is what's broken, an exception here would kill
    the background task silently again, which is the exact failure this exists to remove.
    """
    try:
        client = get_stream_client()
        channel = client.channel(channel_type, channel_id)
        await asyncio.to_thread(channel.send_message, {"text": UNEXPECTED_ERROR_MSG}, AI_USER_ID)
    except Exception:
        logger.exception("Could not report an unexpected failure into channel %s", channel_id)


async def _open_deploy_gate(channel_id: str) -> str | None:
    """The message id of the ship-gate card still open in this channel, or None.

    None covers both "there has never been one" (first pass through generate) and "the group
    already used it", so the caller can treat a returned id as a card that genuinely needs closing.
    """
    state = await asyncio.to_thread(graph.get_state, {"configurable": {"thread_id": channel_id}})
    pending = state.values.get("pending_deploy_approval") or {}
    if pending.get("committed"):
        return None
    return pending.get("message_id")


async def _run_generate_and_report(channel_type: str, channel_id: str) -> None:
    """Background task: source the rule-quality corpus for a channel entering `generate`, then
    report the outcome in the channel. Recreates its own channel handle (mirrors the welcome task)
    so it's safe to fire-and-forget with asyncio.create_task."""
    try:
        result = await run_generate_stage(channel_id)
    except Exception:
        logger.exception("generate stage failed for channel %s", channel_id)
        await _report_unexpected(channel_type, channel_id)
        return
    if result["status"] == "skipped":
        return
    client = get_stream_client()
    channel = client.channel(channel_type, channel_id)
    if result["status"] == "succeeded" and result.get("report"):
        text = CORPUS_SOURCED_MSG.format(n=result["num_posts"]) + "\n\n" + format_report_summary(result["report"])
        await asyncio.to_thread(channel.send_message, {"text": text}, AI_USER_ID)

        # The gate this re-check replaces, read BEFORE the new anchor overwrites the record. A rule
        # edit during `generate` re-runs this stage (see reactions), which leaves the earlier
        # ship-gate card sitting in the scroll under a report that no longer describes the rules.
        # pending_deploy_approval is a single record, so the old anchor id is simply forgotten —
        # nothing marks that card closed and nothing answers a 👍🏾 on it.
        previous = await _open_deploy_gate(channel_id)

        # Post the ship-gate anchor and register the pending deploy approval: reacting to THIS message
        # advances generate -> deploy and materializes the bundle. Mirrors the preview-approval anchor.
        resp = await asyncio.to_thread(
            channel.send_message, approval_anchor(DEPLOY_APPROVAL_PROMPT), AI_USER_ID
        )
        msg_obj = resp.get("message", {}) if isinstance(resp, dict) else {}
        anchor_id = msg_obj.get("id") if isinstance(msg_obj, dict) else getattr(msg_obj, "id", None)
        if anchor_id:
            await asyncio.to_thread(
                graph.update_state,
                {"configurable": {"thread_id": channel_id}},
                {"pending_deploy_approval": {"message_id": anchor_id, "approved_by": []}},
            )
            # Retired only now that its replacement is safely up. Doing it when the re-check STARTS
            # would strand a group whose re-check then failed with no gate at all — the failure
            # path posts CORPUS_FAILED_MSG and registers nothing, so the old card is their only
            # way forward until a later run succeeds.
            if previous and previous != anchor_id:
                await close_path_in_stream(previous, DEPLOY_GATE_SUPERSEDED_NOTE)
    else:
        await asyncio.to_thread(channel.send_message, {"text": CORPUS_FAILED_MSG}, AI_USER_ID)


def _plural(n: int, word: str) -> str:
    """'1 label' / '2 labels' — count with a naively pluralized noun."""
    return f"{n} {word}" if n == 1 else f"{n} {word}s"


async def _post_path_anchor(channel, channel_id: str, text: str, state_key: str) -> str | None:
    """Post one half of the fork and register it as the pending vote under `state_key`.

    A card the group can react to is only a real gate once the pending entry exists — without it
    the reaction handler has nothing to match the message against and the 👍🏾 does nothing. Returns
    the anchor id, or None if Stream didn't give one back (in which case that path is unreachable
    and the other one still works).
    """
    resp = await asyncio.to_thread(channel.send_message, approval_anchor(text), AI_USER_ID)
    msg_obj = resp.get("message", {}) if isinstance(resp, dict) else {}
    anchor_id = msg_obj.get("id") if isinstance(msg_obj, dict) else getattr(msg_obj, "id", None)

    if not anchor_id:
        logger.error("No message id back for the %s anchor in %s", state_key, channel_id)
        return None

    await asyncio.to_thread(
        graph.update_state,
        {"configurable": {"thread_id": channel_id}},
        {state_key: {"message_id": anchor_id, "approved_by": []}},
    )
    return anchor_id


async def close_path_in_stream(message_id: str | None, note: str) -> None:
    """Grey out a card the group can no longer act on and say why, once.

    Two callers, same problem: the fork half the group didn't take, and the ship gate a re-check
    replaced. Something else is what makes the card inert — voting.close_other_path's committed
    flag for the fork, the overwritten pending_deploy_approval record for the gate — this is the
    visible half, so someone scrolling back doesn't react to a card that will never fire. `note`
    differs by which card is being closed; the ways back aren't the same (see messages.py).
    Best-effort: a failed edit costs the note, not the closure.
    """
    if not message_id:
        return
    try:
        await set_approval_state(message_id, APPROVAL_SUPERSEDED)

        client = get_stream_client()
        existing = await asyncio.to_thread(client.get_message, message_id)
        msg = existing.get("message", {}) if isinstance(existing, dict) else {}
        text = msg.get("text") or ""
        if note not in text:
            await _update_stream_message(message_id, f"{text}\n\n{note}")
    except Exception:
        logger.warning("Couldn't close path anchor %s", message_id, exc_info=True)


# Above this many MATCHED labels the inline ' · ' run stops being readable — a chat client wraps it
# at arbitrary points — so each label gets its own bullet instead.
_BREAKDOWN_INLINE_MAX = 4


def _run_breakdown(per_label: dict) -> str:
    """Per-label record breakdown for the sandbox run report.

    Splits matched from unmatched, because a label that fired zero times is a signal in its own
    right (usually a rule that doesn't describe what the group meant) and reads as an omission when
    it's merely absent. The executor now reports zeros explicitly; summaries recorded before it did
    simply have no zero entries, and render as they always did.
    """
    matched = {lid: n for lid, n in (per_label or {}).items() if n}
    unmatched = [lid for lid, n in (per_label or {}).items() if not n]

    if not matched:
        if unmatched:
            return "• No test post matched any label: " + ", ".join(f"`{l}`" for l in unmatched) + "."
        return "• No posts matched any label."

    if len(matched) <= _BREAKDOWN_INLINE_MAX:
        lines = ["• " + " · ".join(f"`{lid}` ×{n}" for lid, n in matched.items())]
    else:
        lines = [f"• `{lid}` ×{n}" for lid, n in matched.items()]

    if unmatched:
        lines.append("• No matches for: " + ", ".join(f"`{l}`" for l in unmatched))
    return "\n".join(lines)


async def _run_deploy_and_report(channel_type: str, channel_id: str) -> None:
    """Background task for the `deploy` stage: materialize the bundle, then run it in the sandbox,
    reporting each step in the channel. Mirrors _run_generate_and_report (own handle, fire-and-forget).
    The sandbox run is a pass/fail machine gate — no vote — so it flows straight through to the report."""
    client = get_stream_client()
    channel = client.channel(channel_type, channel_id)

    # 1. Materialize the bundle.
    try:
        result = await run_deploy_stage(channel_id)
    except Exception:
        logger.exception("deploy stage failed for channel %s", channel_id)
        await _report_unexpected(channel_type, channel_id)
        return
    if result["status"] == "skipped":
        return
    if result["status"] != "succeeded":
        await asyncio.to_thread(channel.send_message, {"text": BUNDLE_FAILED_MSG}, AI_USER_ID)
        return
    await asyncio.to_thread(
        channel.send_message,
        {"text": BUNDLE_READY_MSG.format(label_phrase=_plural(result["labels"], "label"), rules=result["rules"])},
        AI_USER_ID,
    )

    # 2. Run it end-to-end in the sandbox, with a live indicator while the executor works.
    await _set_ai_indicator(channel, "AI_STATE_GENERATING")
    try:
        run = await run_execute_stage(channel_id)
    except Exception:
        logger.exception("execute stage failed for channel %s", channel_id)
        await asyncio.to_thread(channel.send_message, {"text": SANDBOX_RUN_FAILED_MSG}, AI_USER_ID)
        return
    finally:
        await _set_ai_indicator(channel, "clear")

    if run["status"] == "skipped":
        return
    if run["status"] != "succeeded":
        await asyncio.to_thread(channel.send_message, {"text": SANDBOX_RUN_FAILED_MSG}, AI_USER_ID)
        return

    report = SANDBOX_RUN_REPORT.format(
        did=run["did"],
        total=run.get("total", 0),
        record_phrase=_plural(run.get("records_emitted", 0), "signed label record"),
        breakdown=_run_breakdown(run.get("per_label") or {}),
    )
    await asyncio.to_thread(channel.send_message, {"text": report}, AI_USER_ID)

    # The fork: an intro, then one anchor per path. Both are registered as pending votes and
    # whichever reaches a majority first decides — the winner's handler closes the loser. Posted in
    # order so the intro's "the next two messages" reads correctly in the scroll.
    await asyncio.to_thread(channel.send_message, {"text": PATH_CHOICE_INTRO}, AI_USER_ID)

    await _post_path_anchor(channel, channel_id, GUIDE_PATH_PROMPT, "pending_guide_choice")
    await _post_path_anchor(channel, channel_id, GO_LIVE_PATH_PROMPT, "pending_provision_approval")


async def _send_welcome_message(channel_type: str, channel_id: str, user_name: str) -> None:
    """Send a system notification and AI welcome message when a new user joins."""
    client = get_stream_client()
    channel = client.channel(channel_type, channel_id)

    await asyncio.to_thread(client.upsert_user, {"id": AI_USER_ID, "name": AI_USER_NAME})
    await asyncio.to_thread(channel.create, AI_USER_ID)
    await asyncio.to_thread(
        channel.send_message,
        {"text": f"{user_name} joined the channel", "type": "system"},
        AI_USER_ID,
    )

    response = await fast_model.ainvoke([
        SystemMessage(content=WELCOME_MESSAGE_PROMPT),
        HumanMessage(content=f"Welcome {user_name} to the channel."),
    ])

    welcome_text = (response.content or "").strip()
    if welcome_text:
        await asyncio.to_thread(channel.send_message, {"text": welcome_text}, AI_USER_ID)


async def _announce_threshold_change(channel_type: str, channel_id: str) -> None:
    """Say so when the roster change that just happened moved the approval threshold.

    The number CLEO quotes comes from `approvals_needed` in graph state, refreshed once per agent
    run (agent_runner); enforcement recomputes it live from the roster on every reaction
    (reactions._process_approval_reaction). Between a join and the next run those two disagree, and
    the stale figure is already in the scroll under cards that are still open for votes. A group
    that arrives one at a time is told "a single 👍🏾 carries it" and then silently isn't governed
    that way.

    Deliberately read-only on graph state. Correcting the stored figure here would be a bare
    update_state from a webhook, which is exactly the write that races an in-flight run and gets
    erased (see agent_runner._after_run_hooks); the next run refreshes it anyway. The cost is that
    two joins before any run each announce, which is accurate both times.

    Silent before the group has started: with no stored figure, nothing has been promised yet.
    """
    client = get_stream_client()
    channel = client.channel(channel_type, channel_id)

    result = await asyncio.to_thread(channel.query)
    voting_count = len([m for m in result.get("members", []) if is_voting_member(m["user_id"])])
    needed = approvals_needed(voting_count)

    state = await asyncio.to_thread(
        graph.get_state, {"configurable": {"thread_id": channel_id}}
    )
    previous = state.values.get("approvals_needed")
    if previous is None or previous == needed:
        return

    logger.info(
        "Approval threshold for %s moved %s -> %s (%d voting members)",
        channel_id, previous, needed, voting_count,
    )
    await asyncio.to_thread(
        channel.send_message,
        {"text": THRESHOLD_CHANGED_MSG.format(voting_count=voting_count, needed=needed)},
        AI_USER_ID,
    )


async def _on_member_joined(channel_type: str, channel_id: str, user_name: str) -> None:
    """Everything a join owes the channel, in order: the welcome, then the vote arithmetic.

    Sequenced rather than spawned as two tasks so the threshold notice can't land above the
    welcome of the person whose arrival caused it.
    """
    await _send_welcome_message(channel_type, channel_id, user_name)
    await _announce_threshold_change(channel_type, channel_id)


# Human-readable labels for the three collected answers, for the confirm card and the "still to
# settle" line. Keys match maintenance_guide.COLLECTED_KEYS.
_ANSWER_LABELS = {
    "handle": "Name on Bluesky",
    "custodian": "Custodian",
    "appeals": "Appeals contact",
    "backup_custodian": "Backup custodian",
}
_OUTSTANDING_PHRASES = {
    "handle": "what it's called on Bluesky",
    "custodian": "who holds the account",
    "appeals": "who hears appeals",
}


def _format_answers(governance: dict) -> str:
    """Bullet the recorded answers in a stable order. Handles render as @handles."""
    answers = answered_fields(governance)
    lines = []
    for key in ("handle", "custodian", "backup_custodian", "appeals"):
        value = answers.get(key)
        if not value:
            continue
        shown = f"`@{value}`" if key == "handle" else value
        lines.append(f"• *{_ANSWER_LABELS[key]}:* {shown}")
    return "\n".join(lines) if lines else "• (nothing recorded yet)"


def _format_outstanding(keys: list[str]) -> str:
    phrases = [_OUTSTANDING_PHRASES.get(k, k) for k in keys]
    if len(phrases) <= 1:
        return "".join(phrases)
    return ", ".join(phrases[:-1]) + " and " + phrases[-1]


async def _run_provision_and_report(channel_type: str, channel_id: str) -> None:
    """Background task for the `provision` stage: open the governance conversation.

    Posts the three questions with derived handle candidates. No approval anchor here — the group
    answers in chat and _run_governance_capture stages a confirm card from whatever they say.
    """
    try:
        result = await run_provision_stage(channel_id)
    except Exception:
        logger.exception("provision stage failed for channel %s", channel_id)
        await _report_unexpected(channel_type, channel_id)
        return
    if result["status"] == "skipped":
        return

    client = get_stream_client()
    channel = client.channel(channel_type, channel_id)
    await asyncio.to_thread(
        channel.send_message, {"text": provision_stage_intro(result["candidates"])}, AI_USER_ID
    )


async def _run_governance_capture(channel_type: str, channel_id: str) -> None:
    """Background task: extract any governance answers from the group's latest messages and, if
    there are any, post a confirm card and register it for the approval vote.

    Runs on every new message while the channel is in `provision`. Silent when the group was
    talking about something else — the common case — so it never interrupts an ongoing discussion.
    """
    try:
        result = await capture_governance(channel_id)
    except Exception:
        logger.exception("governance capture failed for channel %s", channel_id)
        await _report_unexpected(channel_type, channel_id)
        return

    if result["status"] == "stand_down":
        await _stand_down_and_report(channel_type, channel_id)
        return
    if result["status"] != "staged":
        return

    client = get_stream_client()
    channel = client.channel(channel_type, channel_id)

    # Show the card against the answers as they WOULD be after this proposal lands, so the group
    # confirms the whole picture rather than just the delta they happened to say last.
    graph_config = {"configurable": {"thread_id": channel_id}}
    state = await asyncio.to_thread(graph.get_state, graph_config)
    preview = {**(state.values.get("governance") or {}), **result["proposal"]}

    text = GOVERNANCE_CONFIRM_CARD.format(answers=_format_answers(preview))
    if result["outstanding_after"]:
        text += GOVERNANCE_REMAINING_MSG.format(
            remaining=_format_outstanding(result["outstanding_after"])
        )

    resp = await asyncio.to_thread(channel.send_message, approval_anchor(text), AI_USER_ID)
    msg_obj = resp.get("message", {}) if isinstance(resp, dict) else {}
    anchor_id = msg_obj.get("id") if isinstance(msg_obj, dict) else getattr(msg_obj, "id", None)
    if anchor_id:
        await asyncio.to_thread(
            graph.update_state, graph_config,
            {"pending_governance_suggestions": {
                anchor_id: {"proposal": result["proposal"], "approved_by": []}
            }},
        )
        logger.info("Staged governance confirm card %s for %s", anchor_id, channel_id)


async def _stand_down_and_report(channel_type: str, channel_id: str) -> None:
    """Take a channel back out of `provision` and post the fresh go-live anchor.

    The back edge. Whatever the group already approved stays in `governance` and is echoed back, so
    parking the questions never reads as losing the answers. The message it posts IS the new
    approval anchor — without registering one, "pick it up whenever" would be unactionable, since
    the original anchor is committed and can never fire again.
    """
    try:
        result = await stand_down_provision(channel_id)
    except Exception:
        logger.exception("stand-down failed for channel %s", channel_id)
        await _report_unexpected(channel_type, channel_id)
        return
    if result["status"] == "skipped":
        return

    client = get_stream_client()
    channel = client.channel(channel_type, channel_id)

    governance = result.get("governance") or {}
    kept = ""
    if any(answered_fields(governance).values()):
        kept = PROVISION_KEPT_ANSWERS_MSG.format(answers=_format_answers(governance))
    text = PROVISION_STOOD_DOWN_MSG.format(
        kept=kept, guide_url=f"{FRONTEND_URL}/?guide={channel_id}"
    )

    anchor_id = await _post_path_anchor(channel, channel_id, text, "pending_provision_approval")
    if anchor_id:
        logger.info("Posted fresh go-live anchor %s for %s after stand-down", anchor_id, channel_id)


async def _reopen_go_live_and_report(channel_type: str, channel_id: str) -> None:
    """Reopen the going-live path after the group took the guide, and post a fresh anchor.

    The promise made by PATH_NOT_TAKEN_NOTE and GUIDE_CHOSEN_MSG. The closed anchor is committed
    and can never fire again, so switching paths needs a new card rather than an un-closing of the
    old one — that also keeps the scrollback honest about when the group changed its mind.

    Guarded to `deploy`: at any other stage the going-live question is either not yet reached or
    already answered, and posting an anchor would offer a vote that leads nowhere.
    """
    graph_config = {"configurable": {"thread_id": channel_id}}
    state = await asyncio.to_thread(graph.get_state, graph_config)

    if state.values.get("lifecycle_stage") != "deploy":
        logger.info("Go-live reopen ignored for %s: not at deploy", channel_id)
        return

    pending = state.values.get("pending_provision_approval") or {}
    if pending and not pending.get("committed"):
        logger.info("Go-live reopen ignored for %s: an anchor is already open", channel_id)
        return

    client = get_stream_client()
    channel = client.channel(channel_type, channel_id)

    anchor_id = await _post_path_anchor(
        channel, channel_id, GO_LIVE_REOPENED_MSG, "pending_provision_approval"
    )
    if anchor_id:
        logger.info("Reopened the go-live path for %s under anchor %s", channel_id, anchor_id)
