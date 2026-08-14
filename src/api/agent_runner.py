"""
The brainstorming-graph driver and the per-channel scheduler that decides when to run it.

Two layers:
  * _run_ai_agent — one run: fetch recent history, stream the graph, keep a single Stream message
    updated as tokens arrive, then promote any pending proposal/rules into vote-able suggestions.
  * _schedule_agent / _agent_runner — when runs happen. A channel gets at most one runner at a
    time; triggers that land mid-run COALESCE into a single debounced catch-up. Whether the burst
    addressed CLEO decides only if that catch-up bypasses the router, never whether it happens.

The scheduler relies on a single event-loop worker: the active-check and the add happen with no
await between them, so two triggers can't both start a runner.
"""

import asyncio
import collections
import logging
import os
import time
from collections.abc import Awaitable, Callable

from src.agent.brainstorming.graph import graph
from src.agent.brainstorming.voting import approvals_needed, superseded_entries
from src.api.helpers import get_last_messages_from_channel, messages_to_langchain
from src.api.messages import SUPERSEDED_NOTE
from src.api.stream import (
    AI_USER_ID,
    APPROVAL_PENDING,
    APPROVAL_SUPERSEDED,
    STREAM_DEBOUNCE_SECONDS,
    _ensure_ai_member,
    _set_ai_indicator,
    _update_stream_message,
    get_stream_client,
    set_approval_state,
)

logger = logging.getLogger(__name__)

# One lock per channel: prevents concurrent agent runs on the same thread (held inside _run_ai_agent).
_channel_locks: dict[str, asyncio.Lock] = collections.defaultdict(asyncio.Lock)

# Coalescing state (relies on a single event-loop worker; see _schedule_agent). A channel is
# "active" for the WHOLE lifetime of its runner, including the debounce wait between runs so a
# second trigger can never start a parallel run. _pending_rerun marks that a message arrived while
# a run was in flight and therefore owes a single catch-up run; _pending_forced records whether any
# of those messages ADDRESSED CLEO, which decides only whether the catch-up bypasses the router;
# _last_trigger_ts drives the debounce (wait for the burst to go quiet before the catch-up fires).
_active_channels: set[str] = set()
_pending_rerun: set[str] = set()
_pending_forced: set[str] = set()
_last_trigger_ts: dict[str, float] = {}
# How long the channel must be quiet (no new addressed trigger) before a coalesced catch-up runs.
AGENT_DEBOUNCE_SECONDS = float(os.environ.get("AGENT_DEBOUNCE_SECONDS", "1.5"))

# Follow-up work that writes graph state and therefore must NOT overlap a run.
#
# LangGraph checkpoints the FULL channel snapshot at each super-step, not a per-key delta. A task
# that calls update_state while astream_events is mid-flight writes a checkpoint the running loop
# never sees; the loop then writes its own checkpoint from the snapshot it started with, carrying
# the stale value forward and silently erasing the write.
#
# So the work is queued here instead of being spawned in parallel, and _agent_runner drains it
# between runs while the channel is still marked active (see _drain_after_run).
AfterRunHook = Callable[[str, str], Awaitable[None]]
_after_run_hooks: dict[str, list[AfterRunHook]] = collections.defaultdict(list)


async def _mark_superseded_in_stream(replaced: dict) -> None:
    """Append a superseded note to each proposal message a newer one just replaced.

    The state flag alone makes old votes inert, but the card stays in the scroll looking exactly
    as approvable as it did before. This is the visible half: it tells someone scrolling back why
    reacting there won't do anything. A failed edit costs the note, not the guard.
    """
    client = get_stream_client()
    for message_id in replaced:
        try:
            # Retag first, and outside the note's idempotence check: the color is what carries the
            # "inert" signal on a card whose note already landed in an earlier run.
            await set_approval_state(message_id, APPROVAL_SUPERSEDED)

            existing = await asyncio.to_thread(client.get_message, message_id)
            msg = existing.get("message", {}) if isinstance(existing, dict) else {}
            text = msg.get("text") or ""
            if SUPERSEDED_NOTE in text:
                continue
            await _update_stream_message(message_id, f"{text}\n\n{SUPERSEDED_NOTE}")
        except Exception:
            logger.warning("Couldn't mark message %s superseded", message_id, exc_info=True)


async def _run_ai_agent(channel_type: str, channel_id: str, force_respond: bool = False) -> None:
    """Fetch last 20 messages, run the brainstorming graph with streaming, post to Stream."""

    logger.info(
        "_run_ai_agent called: channel_type=%s channel_id=%s force_respond=%s",
        channel_type, channel_id, force_respond,
    )

    lock = _channel_locks[channel_id]

    if lock.locked():
        logger.info("Agent already running for channel %s, skipping", channel_id)
        return
    
    async with lock:
        try:
            client = get_stream_client()
            channel = client.channel(channel_type, channel_id)

            voting_member_count = await _ensure_ai_member(channel)

            history = await asyncio.to_thread(get_last_messages_from_channel, channel, 20)
            logger.info("Fetched %d messages from channel", len(history))
            langchain_messages = messages_to_langchain(history)

            if not langchain_messages:
                logger.info("No messages to process, skipping")
                return

            message_id = None
            accumulated = []
            last_update = 0.0
            generating_started = False
            message_id_error = False

            try:
                logger.info("Invoking brainstorming graph via astream_events")

                graph_config = {"configurable": {"thread_id": channel_id}}
                existing_state = await asyncio.to_thread(graph.get_state, graph_config)
                # Always set force_respond so a stale True from a previous run
                # doesn't persist in the checkpointer and bypass the router.
                graph_input = {
                    "messages": langchain_messages,
                    "force_respond": force_respond,
                    # Roster-derived, so they can't be read from the checkpoint — recomputed per
                    # run. The count travels with the figure it produced: 2 of 3 and 2 of 2 are
                    # the same number under different rules (see state.voting_member_count).
                    "approvals_needed": approvals_needed(voting_member_count),
                    "voting_member_count": voting_member_count,
                }

                if not existing_state.values:
                    graph_input["labeler_config"] = {}
                    graph_input["setup_stage"] = "purpose"

                async for event in graph.astream_events(
                    graph_input,
                    config=graph_config,
                    version="v2",
                ):
                    # logging called langgraph node 
                    metadata = event.get("metadata", {})
                    node = metadata.get("langgraph_node")

                    if event["event"] == "on_chain_start" and node and not node.startswith("__"):
                        logger.info("→ node: %s", node)

                    if event["event"] == "on_chat_model_start" and node:
                        logger.info("  llm call: %s", node)

                    # router responds — show thinking indicator and send placeholder
                    if event["event"] == "on_chain_start" and node == "validate_and_classify" and message_id is None:
                        await _set_ai_indicator(channel, "AI_STATE_THINKING")

                        resp = await asyncio.to_thread(channel.send_message, {"text": ""}, AI_USER_ID)
                        msg_obj = resp.get("message") if isinstance(resp, dict) else getattr(resp, "message", None)
                        message_id = msg_obj.get("id") if isinstance(msg_obj, dict) else getattr(msg_obj, "id", None)

                        if not message_id:
                            logger.error("Failed to get message_id from Stream response")
                            message_id_error = True
                            break
                    if (
                        event["event"] == "on_chat_model_stream"
                        and node == "draft_response"
                        and message_id
                    ):
                        if not generating_started:
                            await _set_ai_indicator(channel, "AI_STATE_GENERATING")
                            generating_started = True

                        chunk_content = event["data"]["chunk"].content

                        if chunk_content:
                            accumulated.append(chunk_content)
                            now = time.monotonic()
                            if now - last_update >= STREAM_DEBOUNCE_SECONDS:
                                await _update_stream_message(message_id, "".join(accumulated))
                                last_update = now

                if message_id_error:
                    return
                
                if not message_id:
                    logger.info("Router skipped response — no message sent")
                    return
                
                # Final state wins over the token buffer. draft_response appends the proposal,
                # rules and config blocks *after* its own LLM stream ends, so what streamed is
                # only ever a prefix of the real message — sending it would drop the very block
                # the group votes on, including the "react to approve" line. Streaming stays for
                # the live typing effect; this is the authoritative write.
                state_after = await asyncio.to_thread(graph.get_state, graph_config)
                draft_value = state_after.values.get("draft_response") or ""
                draft = (draft_value if isinstance(draft_value, str) else "").strip()
                # Only if the graph somehow wrote nothing — every node that reaches the channel
                # sets draft_response, so this is defensive.
                final_text = draft or "".join(accumulated).strip()

                if not final_text:
                    logger.info("Draft was empty — deleting placeholder message")

                    await asyncio.to_thread(get_stream_client().delete_message, message_id, hard=True)

                    return

                await _update_stream_message(message_id, final_text)

                logger.info(
                    "Graph streaming complete, total chars: %d (streamed %d)",
                    len(final_text), sum(len(c) for c in accumulated),
                )

                # Promote any pending proposal to pending_suggestions keyed by this message_id
                state = await asyncio.to_thread(graph.get_state, graph_config)
                pending_proposal = state.values.get("pending_proposal")

                if pending_proposal:
                    suggestion = {"proposal": pending_proposal, "approved_by": []}
                    replaced = superseded_entries(state.values.get("pending_suggestions"), message_id)

                    await asyncio.to_thread(
                        graph.update_state, graph_config,
                        {
                            "pending_suggestions": {message_id: suggestion, **replaced},
                            "pending_proposal": None,
                        },
                    )
                    await _mark_superseded_in_stream(replaced)
                    # Only now is this bubble known to be an anchor — it was created empty, before
                    # the graph had decided whether the turn would stage anything.
                    await set_approval_state(message_id, APPROVAL_PENDING)

                    logger.info(
                        "Staged pending suggestion under message_id=%s (superseded %d earlier)",
                        message_id, len(replaced),
                    )

                # Promote any pending classification rules to pending_rule_suggestions keyed by this message_id
                pending_classification_rules = state.values.get("pending_classification_rules")

                if pending_classification_rules:
                    rule_suggestion = {"proposal": pending_classification_rules, "approved_by": []}
                    replaced = superseded_entries(
                        state.values.get("pending_rule_suggestions"), message_id
                    )

                    await asyncio.to_thread(
                        graph.update_state, graph_config,
                        {
                            "pending_rule_suggestions": {message_id: rule_suggestion, **replaced},
                            "pending_classification_rules": None,
                        },
                    )
                    await _mark_superseded_in_stream(replaced)
                    await set_approval_state(message_id, APPROVAL_PENDING)

                    logger.info(
                        "Staged pending rule suggestion under message_id=%s (superseded %d earlier)",
                        message_id, len(replaced),
                    )
            except Exception:
                logger.exception("Error during graph streaming")

                await _update_stream_message(message_id, "Sorry, I couldn't generate a response right now.")
            finally:
                await _set_ai_indicator(channel, "clear")
        except Exception:
            logger.exception("Unhandled error in _run_ai_agent")


async def _await_quiet(channel_id: str) -> None:
    """Block until no trigger has landed for AGENT_DEBOUNCE_SECONDS, so a burst of messages
    coalesces into one catch-up run instead of firing on the first one."""
    while True:
        remaining = AGENT_DEBOUNCE_SECONDS - (time.monotonic() - _last_trigger_ts.get(channel_id, 0.0))
        if remaining <= 0:
            return
        await asyncio.sleep(remaining)


async def _drain_after_run(channel_type: str, channel_id: str) -> None:
    """Run the state-writing follow-ups queued for this channel, one at a time, and never
    concurrently with a graph run (see _after_run_hooks for why that matters).

    Deduplicated by identity: a coalesced burst queues the same hook once per message, but every
    hook reads the accumulated history, so running it once covers the whole burst — running it
    twice would post duplicate cards. A failing hook is logged and skipped rather than taking down
    the runner, which still owes the channel its _active_channels cleanup.
    """
    hooks = _after_run_hooks.pop(channel_id, [])
    seen: set[AfterRunHook] = set()
    for hook in hooks:
        if hook in seen:
            continue
        seen.add(hook)
        try:
            await hook(channel_type, channel_id)
        except Exception:
            logger.exception(
                "after-run hook %s failed for channel %s",
                getattr(hook, "__name__", hook), channel_id,
            )


async def _agent_runner(channel_type: str, channel_id: str, force_respond: bool) -> None:
    """Own a channel's agent runs end-to-end: the initial run, then at most one debounced catch-up
    per cycle for any *addressed* messages that arrived while it was busy (coalesced, not one reply
    per message). The router still gates each run, so a catch-up with nothing to say stays silent.

    Also the only place after-run hooks execute. They're drained while the channel is still marked
    active, so _schedule_agent coalesces any trigger landing mid-hook instead of starting a run
    underneath it — the exclusion the hooks depend on to write state safely.
    """
    try:
        while True:
            await _run_ai_agent(channel_type, channel_id, force_respond)
            await _drain_after_run(channel_type, channel_id)
            # A trigger landing after this point starts a
            # fresh runner rather than being lost to the gap.
            if channel_id not in _pending_rerun:
                _active_channels.discard(channel_id)
                return
            _pending_rerun.discard(channel_id)
            # Bypass the router only if something in the coalesced burst actually addressed CLEO.
            # An unaddressed burst still earns its catch-up; the router decides whether to answer.
            force_respond = channel_id in _pending_forced
            _pending_forced.discard(channel_id)
            await _await_quiet(channel_id)  # debounce: let the message burst settle before responding
    except BaseException:
        logger.exception("Agent runner crashed for channel %s", channel_id)
        _active_channels.discard(channel_id)
        _pending_rerun.discard(channel_id)
        _pending_forced.discard(channel_id)
        raise


def _schedule_agent(
    channel_type: str,
    channel_id: str,
    force_respond: bool,
    after_run: AfterRunHook | None = None,
) -> None:
    """Entry point for every agent trigger. Starts a run immediately if the channel is idle; if a
    run is already in flight it COALESCES into one debounced catch-up. Synchronous on purpose: the
    active-check and the add happen with no await between them, so on the single-worker event loop
    two triggers can never both start a runner.

    EVERY trigger coalesces, addressed or not. Unaddressed ones used to be dropped outright, to
    keep CLEO from replying to chatter — but an idle channel already runs the graph on unaddressed
    messages and lets the router decide, so the only thing being dropped was the router's *chance*
    to see them. That made whether a question got answered depend on whether CLEO happened to be
    mid-reply: a group talking over each other (the normal case, and the one a study observes)
    loses exactly the beginner questions asked while CLEO is busy. force_respond is still carried
    separately via _pending_forced, so an unaddressed catch-up faces the router like any other
    unaddressed message and a catch-up with nothing to say stays silent.

    `after_run` queues follow-up work that writes graph state, to be run once this channel's runs
    have finished rather than alongside them. Callers must pair it with force_respond=True: the
    hooks are gated on an addressed trigger, and pairing keeps the queued hook and the run that
    drains it describing the same event.
    """
    if after_run is not None:
        _after_run_hooks[channel_id].append(after_run)
    _last_trigger_ts[channel_id] = time.monotonic()
    if channel_id in _active_channels:
        _pending_rerun.add(channel_id)
        if force_respond:
            _pending_forced.add(channel_id)
        return
    _active_channels.add(channel_id)
    asyncio.create_task(_agent_runner(channel_type, channel_id, force_respond))
