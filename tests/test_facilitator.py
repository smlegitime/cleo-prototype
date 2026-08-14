"""
Tests for facilitator members: present in the conversation, absent from the vote.

A facilitator (a researcher sitting in on a pilot group) is excluded on BOTH sides of the tally —
they don't raise the threshold by being there, and their 👍🏾 can't help clear it. Excluding only
one side would let a facilitator plus a single participant carry a decision the group never reached.

Everything else about them stays ordinary: their messages reach the graph as user turns and can
summon CLEO. That's why they are NOT marked with the `ai-` prefix, which would change all of it.
"""

import os

os.environ.setdefault("STREAM_API_KEY", "test-key")
os.environ.setdefault("STREAM_API_SECRET", "test-secret")

import pytest

from src.agent.brainstorming.voting import approvals_needed
from src.api import stream as stream_mod
from src.api.helpers import get_last_messages_from_channel, messages_to_langchain
from src.api.stream import is_voting_member

FACILITATOR = "dev3-sybille"
MEMBERS = ["dev3-dev1", "dev3-dev2", "dev3-dev3", FACILITATOR, "ai-assistant"]


@pytest.fixture
def with_facilitator(monkeypatch):
    monkeypatch.setattr(stream_mod, "FACILITATOR_USER_IDS", {FACILITATOR})


# --- who counts ---

def test_facilitator_and_ai_are_not_voting_members(with_facilitator):
    assert is_voting_member("dev3-dev1") is True
    assert is_voting_member(FACILITATOR) is False
    assert is_voting_member("ai-assistant") is False


def test_facilitator_does_not_raise_the_threshold(with_facilitator):
    """The point of the feature: 3 participants + 1 facilitator votes like 3, not like 4."""
    voting = [m for m in MEMBERS if is_voting_member(m)]
    assert len(voting) == 3
    assert approvals_needed(len(voting)) == 2  # majority of 3

    # Without the exclusion the roster reads as 4 -> still 2, but 5 participants + 1 facilitator
    # would read as 6 and demand 4. Pin the case where the inflation actually changes the answer.
    assert approvals_needed(5) == 3
    assert approvals_needed(6) == 4


def test_a_pair_plus_a_facilitator_still_needs_both_participants(with_facilitator):
    """Two participants plus a facilitator: the exclusion changes which RULE is in force, even
    though both rules happen to land on 2. Counted, it is a majority of three — and a majority of
    three can be assembled from the facilitator plus ONE participant, which is a decision the pair
    never made. Excluded, the pair stays a pair, and the only two people who can supply those two
    approvals are the participants themselves.

    The count is only half of that guarantee; the other half is the numerator gate in
    reactions._process_approval_reaction, which is what stops the facilitator supplying one.
    """
    members = ["dev3-dev1", "dev3-dev2", FACILITATOR, "ai-assistant"]
    voting = [m for m in members if is_voting_member(m)]

    assert len(voting) == 2
    assert approvals_needed(len(voting)) == 2
    assert not any(is_voting_member(m) for m in [FACILITATOR, "ai-assistant"])


def test_unset_facilitator_list_leaves_the_tally_unchanged(monkeypatch):
    """Default deployment: no facilitators configured, so only CLEO is excluded."""
    monkeypatch.setattr(stream_mod, "FACILITATOR_USER_IDS", set())
    assert len([m for m in MEMBERS if is_voting_member(m)]) == 4
    assert is_voting_member(FACILITATOR) is True


# --- what stays normal ---

def test_facilitator_messages_still_reach_the_graph_as_user_turns(with_facilitator):
    """The reason a facilitator isn't given an `ai-` id: that prefix would route their words into
    the conversation as CLEO's own (role 'assistant') instead of as a participant's."""

    class _FakeChannel:
        def query(self, messages=None):
            return {"messages": [
                {"id": "m1", "user": {"id": FACILITATOR}, "text": "how's it going?"},
                {"id": "m2", "user": {"id": "ai-assistant"}, "text": "Fine!"},
            ]}

    history = get_last_messages_from_channel(_FakeChannel())
    assert [m["role"] for m in history] == ["user", "assistant"]

    roles = [type(m).__name__ for m in messages_to_langchain(history)]
    assert roles == ["HumanMessage", "AIMessage"]
