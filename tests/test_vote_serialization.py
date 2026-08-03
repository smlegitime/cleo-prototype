"""
Regression test for the concurrent-vote read-modify-write race.

Two members reacting 👍🏾 at nearly the same time each read the same approved_by, add themselves, and
write back — without serialization the second write clobbers the first and a vote is lost. The
webhook handler now resolves votes under a per-channel lock (_vote_locks); this test drives two
concurrent votes through that lock against the real in-memory graph and asserts both are counted.

Uses the real MemorySaver graph (forced by conftest), so it exercises the actual read-modify-write
rather than a mock. STREAM_* are set before importing chatbot, which requires them at module load.
"""

import asyncio
import os

os.environ.setdefault("STREAM_API_KEY", "test-key")
os.environ.setdefault("STREAM_API_SECRET", "test-secret")

import pytest

from src.api import chatbot
from src.agent.brainstorming.voting import process_approval_vote
from src.agent.brainstorming.graph import graph

PROPOSAL = {"display_name": "L", "description": "d", "labels": []}


@pytest.mark.asyncio
async def test_concurrent_votes_are_all_counted_under_lock():
    channel_id = "vote-race"
    message_id = "m1"
    config = {"configurable": {"thread_id": channel_id}}
    # Seed a pending proposal. voting_count=5 => threshold is >2.5 (needs 3), so two votes accumulate
    # without committing — isolating the approved_by read-modify-write we care about.
    graph.update_state(config, {"pending_suggestions": {message_id: {"proposal": PROPOSAL, "approved_by": []}}})

    async def vote(user_id: str):
        # Mirror the webhook's serialization of the vote tally.
        async with chatbot._vote_locks[channel_id]:
            return await process_approval_vote(channel_id, message_id, user_id, voting_member_count=5)

    await asyncio.gather(vote("user-a"), vote("user-b"))

    entry = graph.get_state(config).values["pending_suggestions"][message_id]
    assert set(entry["approved_by"]) == {"user-a", "user-b"}  # neither vote lost
    assert entry.get("committed") is not True  # 2 of 5 hasn't met the majority threshold


@pytest.mark.asyncio
async def test_same_member_double_react_counts_once():
    channel_id = "vote-dedup"
    message_id = "m1"
    config = {"configurable": {"thread_id": channel_id}}
    graph.update_state(config, {"pending_suggestions": {message_id: {"proposal": PROPOSAL, "approved_by": []}}})

    async def vote():
        async with chatbot._vote_locks[channel_id]:
            return await process_approval_vote(channel_id, message_id, "user-a", voting_member_count=5)

    await asyncio.gather(vote(), vote())

    entry = graph.get_state(config).values["pending_suggestions"][message_id]
    assert entry["approved_by"] == ["user-a"]  # one voter, counted once
