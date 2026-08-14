"""
Tests for the sub-threshold vote report: the reply that tells a group where a counted 👍🏾 stands.

The gap this closes is not a wrong tally — it's a silent one. Every process_* gate returns the same
falsy value whether it ignored a reaction or recorded it and is still waiting, and nothing else in
the channel shows a partial count: set_approval_state only ever fires on APPROVED, and the card
carries no number. So a first vote of two changed nothing the group could see, which is
indistinguishable from a broken reaction.

anchor_vote_progress is pure and tested directly; _report_vote_progress is tested against a mocked
graph + channel, so no LangGraph state or Stream call is touched.
"""

import os
from unittest.mock import AsyncMock, MagicMock, patch

os.environ.setdefault("STREAM_API_KEY", "test-key")
os.environ.setdefault("STREAM_API_SECRET", "test-secret")

import pytest

from src.agent.brainstorming.voting import anchor_vote_progress

MSG = "msg-live"
CH = "chan-progress"


def _live(message_id=MSG, approved_by=()):
    return {"message_id": message_id, "approved_by": list(approved_by)}


# ---------------------------------------------------------------- anchor_vote_progress


def test_reports_kind_and_count_for_a_live_setup_proposal():
    state = {"pending_suggestions": {MSG: {"proposal": {}, "approved_by": ["u1"]}}}
    assert anchor_vote_progress(state, MSG) == ("proposal", 1)


def test_reports_a_live_lifecycle_gate():
    """The 'one' shape stores the anchor as a single record keyed by an inner message_id."""
    state = {"pending_preview_approval": _live(approved_by=["u1", "u2"])}
    assert anchor_vote_progress(state, MSG) == ("preview", 2)


def test_a_gate_anchored_on_a_different_message_is_not_this_vote():
    state = {"pending_preview_approval": _live(message_id="other-msg", approved_by=["u1"])}
    assert anchor_vote_progress(state, MSG) is None


def test_no_progress_for_a_reaction_on_ordinary_chat():
    assert anchor_vote_progress({"pending_suggestions": {}}, MSG) is None
    assert anchor_vote_progress({}, MSG) is None


def test_committed_and_superseded_cards_report_nothing():
    """Both already have a handler that speaks for them; a tally here would talk over it."""
    committed = {"pending_suggestions": {MSG: {"approved_by": ["u1", "u2"], "committed": True}}}
    superseded = {"pending_suggestions": {MSG: {"approved_by": ["u1"], "superseded": True}}}
    assert anchor_vote_progress(committed, MSG) is None
    assert anchor_vote_progress(superseded, MSG) is None


def test_zero_votes_is_reported_as_zero_not_as_absent():
    """A live card nobody has voted on must stay distinguishable from no card at all."""
    assert anchor_vote_progress({"pending_deploy_approval": _live()}, MSG) == ("deploy", 0)


# ---------------------------------------------------------------- _report_vote_progress


def _patched(state_values):
    """Patch the graph the reporter reads and hand back the channel it would post to."""
    channel = MagicMock()
    state = MagicMock()
    state.values = state_values
    graph = MagicMock()
    graph.get_state.return_value = state
    return channel, patch("src.api.reactions.graph", graph)


async def _report(channel, voting_count):
    from src.api.reactions import _report_vote_progress

    await _report_vote_progress(channel, CH, MSG, voting_count)


def _sent(channel):
    return [c.args[0]["text"] for c in channel.send_message.call_args_list]


@pytest.mark.asyncio
async def test_first_of_two_votes_gets_a_reply():
    channel, patched = _patched({"pending_suggestions": {MSG: {"approved_by": ["u1"]}}})
    with patched:
        await _report(channel, voting_count=3)  # 3 voting members -> majority of 2

    assert len(_sent(channel)) == 1
    assert "1 of 2" in _sent(channel)[0]


@pytest.mark.asyncio
async def test_reaction_on_ordinary_chat_stays_silent():
    channel, patched = _patched({"pending_suggestions": {}})
    with patched:
        await _report(channel, voting_count=3)

    assert _sent(channel) == []


@pytest.mark.asyncio
async def test_a_vote_that_carried_is_left_to_its_own_confirmation():
    """The threshold was met on this very reaction, so a gate above already announced it. A count
    posted now would follow '✅ approved' with a card that still sounds unfinished."""
    channel, patched = _patched({"pending_suggestions": {MSG: {"approved_by": ["u1", "u2"]}}})
    with patched:
        await _report(channel, voting_count=3)

    assert _sent(channel) == []


@pytest.mark.asyncio
async def test_a_one_member_channel_never_reports_progress():
    """With a threshold of 1 there is no sub-threshold state to be in: the first 👍🏾 carries."""
    channel, patched = _patched({"pending_suggestions": {MSG: {"approved_by": ["u1"]}}})
    with patched:
        await _report(channel, voting_count=1)

    assert _sent(channel) == []


@pytest.mark.asyncio
async def test_a_pair_hears_that_it_is_waiting_on_the_other_member():
    """Two voting members are unanimous-or-nothing, so a pair has a sub-threshold state now — and
    it is the state a no-show session sits in permanently. Saying so is what makes the stall
    visible instead of looking like a card nobody got round to."""
    channel, patched = _patched({"pending_suggestions": {MSG: {"approved_by": ["u1"]}}})
    with patched:
        await _report(channel, voting_count=2)

    assert "1 of 2" in _sent(channel)[0]


@pytest.mark.asyncio
async def test_facilitator_exclusion_reaches_the_reported_number():
    """3 participants + 1 facilitator: voting_count is 3, so the group hears 'of 2', not 'of 3'."""
    channel, patched = _patched({"pending_rule_suggestions": {MSG: {"approved_by": ["u1"]}}})
    with patched:
        await _report(channel, voting_count=3)

    assert "1 of 2" in _sent(channel)[0]
    assert "of 3" not in _sent(channel)[0]
