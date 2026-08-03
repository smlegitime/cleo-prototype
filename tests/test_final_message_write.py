"""
Tests for what _run_ai_agent actually writes to the channel at the end of a run.

The token stream is a preview, not the message. draft_response appends the proposal, rules and
config blocks AFTER its own LLM stream ends, so anything assembled from the streamed chunks alone
is a truncated draft — it drops the very card the group votes on, "react to approve" line and all.
These pin the final write to the graph's own draft_response.
"""

import os

os.environ.setdefault("STREAM_API_KEY", "test-key")
os.environ.setdefault("STREAM_API_SECRET", "test-secret")

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from langchain_core.messages import HumanMessage

CHANNEL_ID = "channel-test"
MESSAGE_ID = "msg-1"

PROSE = "Perfect — I have everything I need for now."
BLOCK = "📋 **Proposed update**\n\nReact with 👍🏾 to approve this change."
FULL_DRAFT = f"{PROSE}\n\n{BLOCK}"


def _event(name: str, node: str, chunk_content: str | None = None) -> dict:
    event = {"event": name, "metadata": {"langgraph_node": node}}
    if chunk_content is not None:
        event["data"] = {"chunk": type("Chunk", (), {"content": chunk_content})()}
    return event


def _graph(events: list[dict], **state_values) -> MagicMock:
    """A graph whose run emits `events` and whose final state holds `state_values`."""
    mock_graph = MagicMock()

    async def astream_events(*_args, **_kwargs):
        for event in events:
            yield event

    mock_graph.astream_events = astream_events

    state = MagicMock()
    state.values = {
        "draft_response": None,
        "pending_proposal": None,
        "pending_classification_rules": None,
        "pending_suggestions": {},
        "pending_rule_suggestions": {},
        **state_values,
    }
    mock_graph.get_state.return_value = state
    return mock_graph


async def _run(mock_graph: MagicMock, update: AsyncMock, client: MagicMock | None = None) -> None:
    from src.api.agent_runner import _run_ai_agent

    client = client or MagicMock()
    client.channel.return_value.send_message.return_value = {"message": {"id": MESSAGE_ID}}

    with patch("src.api.agent_runner.get_stream_client", return_value=client), \
         patch("src.api.agent_runner.graph", mock_graph), \
         patch("src.api.agent_runner._ensure_ai_member", AsyncMock(return_value=3)), \
         patch("src.api.agent_runner.get_last_messages_from_channel", MagicMock(return_value=[{}])), \
         patch("src.api.agent_runner.messages_to_langchain",
               MagicMock(return_value=[HumanMessage(content="is that all you need?")])), \
         patch("src.api.agent_runner._set_ai_indicator", AsyncMock()), \
         patch("src.api.agent_runner.set_approval_state", AsyncMock()), \
         patch("src.api.agent_runner._update_stream_message", update):
        await _run_ai_agent("messaging", CHANNEL_ID, force_respond=True)


def _final_text(update: AsyncMock) -> str:
    return update.await_args.args[1]


@pytest.mark.asyncio
async def test_the_final_write_is_the_state_draft_not_the_token_buffer():
    """The streamed prose is only the prefix. Sending it verbatim is what dropped the proposal
    card — the group saw CLEO's summary of a proposal with no way to approve it."""
    update = AsyncMock()
    events = [
        _event("on_chain_start", "validate_and_classify"),
        _event("on_chat_model_stream", "draft_response", PROSE),
    ]

    await _run(_graph(events, draft_response=FULL_DRAFT), update)

    assert _final_text(update) == FULL_DRAFT
    assert "React with 👍🏾 to approve this change." in _final_text(update)


@pytest.mark.asyncio
async def test_tokens_still_stream_while_the_draft_is_being_written():
    """The final write replaces the streamed text; it doesn't replace the streaming."""
    update = AsyncMock()
    events = [
        _event("on_chain_start", "validate_and_classify"),
        _event("on_chat_model_stream", "draft_response", PROSE),
    ]

    await _run(_graph(events, draft_response=FULL_DRAFT), update)

    assert update.await_count >= 2
    assert update.await_args_list[0].args[1] == PROSE


@pytest.mark.asyncio
async def test_a_turn_that_streamed_nothing_sends_its_draft():
    """acknowledge_pending and the feedback passthrough write draft_response with no LLM call at
    all, so the run ends with an empty token buffer and a full message to send."""
    update = AsyncMock()
    events = [_event("on_chain_start", "validate_and_classify")]

    await _run(_graph(events, draft_response="📌 There's a labeler proposal above…"), update)

    assert _final_text(update) == "📌 There's a labeler proposal above…"


@pytest.mark.asyncio
async def test_an_empty_draft_deletes_the_placeholder():
    """The placeholder bubble is posted before the graph has decided whether to say anything."""
    update = AsyncMock()
    client = MagicMock()
    events = [_event("on_chain_start", "validate_and_classify")]

    await _run(_graph(events, draft_response="   "), update, client=client)

    client.delete_message.assert_called_once_with(MESSAGE_ID, hard=True)
    update.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_router_skip_never_posts_a_message():
    """No validate_and_classify means the router ended the run — nothing to update or delete."""
    update = AsyncMock()
    client = MagicMock()

    await _run(_graph([], draft_response="stale draft from a previous turn"), update, client=client)

    update.assert_not_awaited()
    client.delete_message.assert_not_called()
