"""
Tests for the join-time approval-threshold notice.

The number CLEO quotes lives in graph state and is refreshed once per agent run; enforcement
recomputes it live from the roster on every reaction. A group that arrives one at a time can
therefore be told "a single 👍🏾 carries it" and then not be governed that way — with the stale
sentence still sitting in the scroll above cards that are open for votes.

Stream and the graph are both mocked, so nothing here touches a real roster or checkpoint.
"""

import os
from unittest.mock import MagicMock, patch

os.environ.setdefault("STREAM_API_KEY", "test-key")
os.environ.setdefault("STREAM_API_SECRET", "test-secret")

import pytest

CH_TYPE = "messaging"
CH = "chan-threshold"
FACILITATOR = "chan-threshold-sybille"


def _members(*user_ids):
    return {"members": [{"user_id": u} for u in user_ids]}


def _patched(roster, stored_needed):
    """Wire a channel roster and the previously-announced figure; return the channel it posts to."""
    channel = MagicMock()
    channel.query.return_value = roster
    client = MagicMock()
    client.channel.return_value = channel

    state = MagicMock()
    state.values = {"approvals_needed": stored_needed}
    graph = MagicMock()
    graph.get_state.return_value = state

    return channel, (
        patch("src.api.reporters.get_stream_client", return_value=client),
        patch("src.api.reporters.graph", graph),
    )


async def _announce(roster, stored_needed):
    from src.api.reporters import _announce_threshold_change

    channel, (patch_client, patch_graph) = _patched(roster, stored_needed)
    with patch_client, patch_graph:
        await _announce_threshold_change(CH_TYPE, CH)
    return [c.args[0]["text"] for c in channel.send_message.call_args_list]


@pytest.mark.asyncio
async def test_third_participant_joining_raises_the_bar_and_says_so():
    """Two members needed 1 👍🏾; the third makes it 2, and the promise already made is now wrong."""
    sent = await _announce(_members("u1", "u2", "u3", "ai-assistant"), stored_needed=1)

    assert len(sent) == 1
    assert "3 of you" in sent[0]
    assert "2 👍🏾" in sent[0]


@pytest.mark.asyncio
async def test_a_join_that_changes_nothing_is_not_announced():
    """4 voting members still need 3... but 3 -> 3 here: no change, no interruption."""
    sent = await _announce(_members("u1", "u2", "u3", "u4", "u5", "ai-assistant"), stored_needed=3)

    assert sent == []


@pytest.mark.asyncio
async def test_a_facilitator_joining_is_never_announced():
    """The whole point of the exclusion: a facilitator's arrival must not move the group's bar.

    Sized so the exclusion is what decides. Five participants need 3; counting the facilitator
    would read as six and demand 4, which is a change, which would announce.
    """
    roster = _members("u1", "u2", "u3", "u4", "u5", FACILITATOR, "ai-assistant")
    with patch("src.api.stream.FACILITATOR_USER_IDS", {FACILITATOR}):
        sent = await _announce(roster, stored_needed=3)

    assert sent == []


@pytest.mark.asyncio
async def test_silent_before_the_group_has_started():
    """No stored figure means nothing has been promised yet, so there is nothing to correct —
    otherwise every pilot would open with vote arithmetic before anyone has said hello."""
    sent = await _announce(_members("u1", "u2", "u3", "ai-assistant"), stored_needed=None)

    assert sent == []


@pytest.mark.asyncio
async def test_a_shrinking_roster_is_announced_too():
    """Shrinking lowers the bar as silently as growing raises it: 5 voting members need 3, and at
    3 they need 2. A group that has lost people is still owed the arithmetic."""
    sent = await _announce(_members("u1", "u2", "u3", "ai-assistant"), stored_needed=3)

    assert len(sent) == 1
    assert "2 👍🏾" in sent[0]


@pytest.mark.asyncio
async def test_dropping_to_a_pair_no_longer_collapses_the_threshold():
    """Under the old majority-of-two rule this was the dangerous case: 3 voting members dropping
    to 2 took the threshold to 1, so whoever reacted first carried every card for the rest of the
    session, announced by nothing. Both members are now required, so the bar doesn't move at all
    and there is nothing to announce — the fix is the silence here, not a message."""
    sent = await _announce(_members("u1", "u2", "ai-assistant"), stored_needed=2)

    assert sent == []


@pytest.mark.asyncio
async def test_graph_state_is_not_written():
    """A bare update_state from a webhook races an in-flight run and gets erased (see
    agent_runner._after_run_hooks). The next run refreshes the figure anyway."""
    from src.api.reporters import _announce_threshold_change

    channel, (patch_client, patch_graph) = _patched(
        _members("u1", "u2", "u3", "ai-assistant"), stored_needed=1
    )
    with patch_client, patch_graph as graph:
        await _announce_threshold_change(CH_TYPE, CH)
        graph.update_state.assert_not_called()
