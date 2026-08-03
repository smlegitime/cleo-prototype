"""
Tests for the approval_state tag that colors a vote card in the client.

The tag is cosmetic — the authoritative tally lives in the checkpoint — but it is only useful if it
tracks the vote. A card left permanently "pending" fills the scroll with bubbles that look
actionable and aren't, which is worse than no color at all. So these tests are mostly about the
RETAGGING: that resolving a vote settles its anchor, and that a superseded card stops advertising
itself even when the run that replaced it had already appended the note.
"""

import asyncio
import os

os.environ.setdefault("STREAM_API_KEY", "test-key")
os.environ.setdefault("STREAM_API_SECRET", "test-secret")

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from src.api.stream import (
    APPROVAL_APPROVED,
    APPROVAL_PENDING,
    APPROVAL_SUPERSEDED,
    approval_anchor,
)

CHANNEL_ID = "channel-test"
ANCHOR_ID = "msg-anchor"


# --- the payload helper ---

def test_anchor_payload_carries_the_text_and_the_pending_tag():
    assert approval_anchor("React 👍🏾 to ship it") == {
        "text": "React 👍🏾 to ship it",
        "approval_state": APPROVAL_PENDING,
    }


# --- superseding ---

@pytest.mark.asyncio
async def test_superseding_retags_the_old_card():
    from src.api.agent_runner import _mark_superseded_in_stream

    client = MagicMock()
    client.get_message.return_value = {"message": {"text": "Here's the proposal"}}
    tag = AsyncMock()

    with patch("src.api.agent_runner.get_stream_client", return_value=client), \
         patch("src.api.agent_runner._update_stream_message", AsyncMock()), \
         patch("src.api.agent_runner.set_approval_state", tag):
        await _mark_superseded_in_stream({"msg-old": {"superseded": True}})

    tag.assert_awaited_once_with("msg-old", APPROVAL_SUPERSEDED)


@pytest.mark.asyncio
async def test_a_card_whose_note_already_landed_is_still_retagged():
    """The note is appended once and skipped thereafter. The color must not be skipped with it, or a
    card superseded by an earlier run keeps the amber it was staged with forever."""
    from src.api.agent_runner import _mark_superseded_in_stream
    from src.api.messages import SUPERSEDED_NOTE

    client = MagicMock()
    client.get_message.return_value = {"message": {"text": f"Proposal\n\n{SUPERSEDED_NOTE}"}}
    update = AsyncMock()
    tag = AsyncMock()

    with patch("src.api.agent_runner.get_stream_client", return_value=client), \
         patch("src.api.agent_runner._update_stream_message", update), \
         patch("src.api.agent_runner.set_approval_state", tag):
        await _mark_superseded_in_stream({"msg-old": {"superseded": True}})

    update.assert_not_awaited()                                    # note not duplicated
    tag.assert_awaited_once_with("msg-old", APPROVAL_SUPERSEDED)    # color still applied


# --- resolving a vote ---

def _stream_client_with_members(n: int = 2) -> MagicMock:
    """A Stream client whose channel reports n non-AI members, as the vote path queries."""
    client = MagicMock()
    members = [{"user_id": f"user-{i}"} for i in range(n)]
    client.channel.return_value.query.return_value = {"members": members}
    return client


def _graph_at(lifecycle_stage: str | None, setup_stage: str = "complete") -> MagicMock:
    mock_graph = MagicMock()
    state = MagicMock()
    state.values = {
        "setup_stage": setup_stage,
        "lifecycle_stage": lifecycle_stage,
        "classification_rules": {"spam": {}},
        "labeler_config": {},
    }
    mock_graph.get_state.return_value = state
    return mock_graph


@pytest.mark.asyncio
async def test_an_approved_rules_card_is_retagged_approved():
    from src.api.reactions import _process_approval_reaction

    tag = AsyncMock()
    # Already in preview both before and after, so this is a rules EDIT during preview — it doesn't
    # re-enter the stage and so doesn't post a fresh preview anchor.
    with patch("src.api.reactions.get_stream_client", return_value=_stream_client_with_members()), \
         patch("src.api.reactions.graph", _graph_at("preview")), \
         patch("src.api.reactions.process_approval_vote", AsyncMock(return_value="rules")), \
         patch("src.api.reactions.set_approval_state", tag):
        await _process_approval_reaction("messaging", CHANNEL_ID, ANCHOR_ID, "user-0")

    tag.assert_awaited_once_with(ANCHOR_ID, APPROVAL_APPROVED)


@pytest.mark.asyncio
async def test_an_approved_lifecycle_gate_is_retagged_approved():
    """The gates don't go through process_approval_vote — each checks its own anchor, so each needs
    its own retag. Covers the preview -> generate gate."""
    from src.api.reactions import _process_approval_reaction

    tag = AsyncMock()
    with patch("src.api.reactions.get_stream_client", return_value=_stream_client_with_members()), \
         patch("src.api.reactions.graph", _graph_at("preview")), \
         patch("src.api.reactions.process_approval_vote", AsyncMock(return_value=None)), \
         patch("src.api.reactions.is_superseded", AsyncMock(return_value=False)), \
         patch("src.api.reactions.process_preview_approval", AsyncMock(return_value=True)), \
         patch("src.api.reactions._run_generate_and_report", AsyncMock()), \
         patch("src.api.reactions.set_approval_state", tag):
        await _process_approval_reaction("messaging", CHANNEL_ID, ANCHOR_ID, "user-0")
        await asyncio.sleep(0)  # let the backgrounded generate task start

    tag.assert_awaited_once_with(ANCHOR_ID, APPROVAL_APPROVED)


@pytest.mark.asyncio
async def test_a_vote_short_of_the_threshold_leaves_the_card_pending():
    """One 👍🏾 of two needed: the card still needs the group, so it must keep its pending color."""
    from src.api.reactions import _process_approval_reaction

    tag = AsyncMock()
    with patch("src.api.reactions.get_stream_client", return_value=_stream_client_with_members(3)), \
         patch("src.api.reactions.graph", _graph_at("preview")), \
         patch("src.api.reactions.process_approval_vote", AsyncMock(return_value=None)), \
         patch("src.api.reactions.is_superseded", AsyncMock(return_value=False)), \
         patch("src.api.reactions.process_preview_approval", AsyncMock(return_value=False)), \
         patch("src.api.reactions.process_deploy_approval", AsyncMock(return_value=False)), \
         patch("src.api.reactions.process_provision_approval", AsyncMock(return_value=False)), \
         patch("src.api.reactions.process_governance_approval", AsyncMock(return_value=None)), \
         patch("src.api.reactions.set_approval_state", tag):
        await _process_approval_reaction("messaging", CHANNEL_ID, ANCHOR_ID, "user-0")

    tag.assert_not_awaited()


# --- the tag write itself ---

@pytest.mark.asyncio
async def test_a_failed_tag_write_does_not_break_the_run():
    """Cosmetic and best-effort, like the superseded note: losing the color must never cost a vote."""
    from src.api.stream import set_approval_state

    client = MagicMock()
    client.update_message_partial.side_effect = Exception("Stream down")

    with patch("src.api.stream.get_stream_client", return_value=client):
        await set_approval_state(ANCHOR_ID, APPROVAL_PENDING)  # must not raise


@pytest.mark.asyncio
async def test_the_tag_is_a_partial_update_so_it_cannot_clobber_text():
    """A streamed card is still accumulating text when it gets tagged."""
    from src.api.stream import set_approval_state

    client = MagicMock()
    with patch("src.api.stream.get_stream_client", return_value=client):
        await set_approval_state(ANCHOR_ID, APPROVAL_PENDING)

    args = client.update_message_partial.call_args.args
    assert args[0] == ANCHOR_ID
    assert args[1] == {"set": {"approval_state": APPROVAL_PENDING}}
    assert "text" not in args[1]["set"]
