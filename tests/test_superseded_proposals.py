"""
Tests for the two halves of "one live proposal at a time".

1. Superseding: staging a revised proposal makes every earlier uncommitted one inert, so a 👍🏾 on
   an older card in the scroll can't commit a design the group already moved past.
2. Acknowledging: a summon that asks for nothing points at the pending vote instead of deriving a
   second, near-identical proposal to split it.
"""

import os

os.environ.setdefault("STREAM_API_KEY", "test-key")
os.environ.setdefault("STREAM_API_SECRET", "test-secret")

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from src.agent.brainstorming.nodes import (
    NOTHING_PENDING_MSG,
    _live_pending_anchor,
    acknowledge_pending,
    validate_and_classify,
)
from src.agent.brainstorming.voting import (
    MAJORITY_THRESHOLD,
    approvals_needed,
    is_superseded,
    process_approval_vote,
    superseded_entries,
)

PROPOSAL = {"display_name": "Test Labeler", "description": "A test labeler", "labels": []}
RULES = {"spam": {"label_identifier": "spam", "include_groups": [], "exclude_signals": [], "notes": None}}
CHANNEL_ID = "channel-test"


def _state(**values):
    mock_state = MagicMock()
    mock_state.values = {
        "pending_suggestions": {},
        "pending_rule_suggestions": {},
        "labeler_config": {},
        "setup_stage": None,
        "classification_rules": {},
        **values,
    }
    return mock_state


# --- superseded_entries ---

def test_marks_every_other_live_suggestion():
    suggestions = {
        "msg-old": {"proposal": PROPOSAL, "approved_by": []},
        "msg-older": {"proposal": PROPOSAL, "approved_by": ["user-1"]},
        "msg-new": {"proposal": PROPOSAL, "approved_by": []},
    }
    replaced = superseded_entries(suggestions, "msg-new")

    assert set(replaced) == {"msg-old", "msg-older"}
    assert all(entry["superseded"] for entry in replaced.values())
    # Existing votes are preserved on the entry — superseding is not a reset.
    assert replaced["msg-older"]["approved_by"] == ["user-1"]


def test_leaves_committed_and_already_superseded_alone():
    suggestions = {
        "msg-committed": {"proposal": PROPOSAL, "approved_by": ["user-1"], "committed": True},
        "msg-superseded": {"proposal": PROPOSAL, "approved_by": [], "superseded": True},
    }
    assert superseded_entries(suggestions, "msg-new") == {}


def test_empty_and_missing_suggestions_are_safe():
    assert superseded_entries(None, "msg-new") == {}
    assert superseded_entries({}, "msg-new") == {}


# --- the vote guard ---

@pytest.mark.asyncio
async def test_approving_a_superseded_proposal_commits_nothing():
    """The bug this exists to prevent: a 👍🏾 on an older card committing a stale design."""
    suggestions = {"msg-old": {"proposal": PROPOSAL, "approved_by": [], "superseded": True}}

    with patch("src.agent.brainstorming.voting.graph") as mock_graph, \
         patch("src.agent.brainstorming.voting.commit_proposal") as mock_commit:
        mock_graph.get_state.return_value = _state(pending_suggestions=suggestions)
        result = await process_approval_vote(CHANNEL_ID, "msg-old", "user-1", MAJORITY_THRESHOLD)

    assert result is None
    mock_commit.assert_not_called()
    mock_graph.update_state.assert_not_called()  # not even the tally moves


@pytest.mark.asyncio
async def test_the_newest_proposal_still_commits_normally():
    suggestions = {
        "msg-old": {"proposal": PROPOSAL, "approved_by": [], "superseded": True},
        "msg-new": {"proposal": PROPOSAL, "approved_by": []},
    }

    with patch("src.agent.brainstorming.voting.graph") as mock_graph, \
         patch("src.agent.brainstorming.voting.commit_proposal", return_value=PROPOSAL) as mock_commit:
        mock_graph.get_state.return_value = _state(pending_suggestions=suggestions)
        result = await process_approval_vote(CHANNEL_ID, "msg-new", "user-1", MAJORITY_THRESHOLD)

    assert result == "proposal"
    mock_commit.assert_called_once()


@pytest.mark.asyncio
async def test_is_superseded_distinguishes_replaced_from_live():
    suggestions = {
        "msg-old": {"proposal": PROPOSAL, "approved_by": [], "superseded": True},
        "msg-new": {"proposal": PROPOSAL, "approved_by": []},
    }
    with patch("src.agent.brainstorming.voting.graph") as mock_graph:
        mock_graph.get_state.return_value = _state(pending_suggestions=suggestions)
        assert await is_superseded(CHANNEL_ID, "msg-old") is True
        assert await is_superseded(CHANNEL_ID, "msg-new") is False
        assert await is_superseded(CHANNEL_ID, "msg-unknown") is False


# --- approvals_needed ---

def test_approvals_needed_matches_the_threshold_rule():
    assert approvals_needed(1) == 1
    assert approvals_needed(2) == 1
    assert approvals_needed(3) == 2   # majority of 3
    assert approvals_needed(5) == 3   # majority of 5


# --- _live_pending_anchor ---

def test_anchor_is_none_when_everything_is_settled():
    assert _live_pending_anchor({
        "pending_suggestions": {"a": {"proposal": PROPOSAL, "committed": True}},
        "pending_rule_suggestions": {"b": {"proposal": RULES, "superseded": True}},
    }) is None


def test_anchor_picks_the_newest_live_suggestion():
    kind, suggestion = _live_pending_anchor({
        "pending_suggestions": {
            "msg-old": {"proposal": PROPOSAL, "approved_by": ["user-1"], "superseded": True},
            "msg-new": {"proposal": PROPOSAL, "approved_by": ["user-2"]},
        },
    })
    assert kind == "proposal"
    assert suggestion["approved_by"] == ["user-2"]


def test_rules_anchor_wins_over_an_older_config_proposal():
    kind, _ = _live_pending_anchor({
        "pending_suggestions": {"msg-cfg": {"proposal": PROPOSAL, "approved_by": []}},
        "pending_rule_suggestions": {"msg-rules": {"proposal": RULES, "approved_by": []}},
    })
    assert kind == "rules"


# --- acknowledge_pending ---

def test_acknowledgement_reports_the_tally_not_a_new_proposal():
    text = acknowledge_pending({
        "pending_suggestions": {"msg-1": {"proposal": PROPOSAL, "approved_by": ["user-1"]}},
        "approvals_needed": 2,
    })["draft_response"]

    assert "1 of 2 approvals" in text
    assert "labeler proposal" in text
    assert "👍🏾" in text


def test_acknowledgement_says_none_yet_when_nobody_has_voted():
    text = acknowledge_pending({
        "pending_rule_suggestions": {"msg-1": {"proposal": RULES, "approved_by": []}},
        "approvals_needed": 3,
    })["draft_response"]

    assert "no approvals yet" in text
    assert "needs 3" in text
    assert "classification rules" in text


def test_acknowledgement_never_goes_silent_without_an_anchor():
    assert acknowledge_pending({})["draft_response"] == NOTHING_PENDING_MSG


# --- routing ---

def _classified(intent: str, atproto: str = "labeler"):
    return {"violation": False, "message": "", "intent": intent, "atproto": atproto, "topic": "t"}


def _route(intent: str, state: dict, atproto: str = "labeler") -> str:
    llm = MagicMock()
    llm.invoke.return_value = _classified(intent, atproto)
    with patch("src.agent.brainstorming.nodes._validate_and_classify_llm", llm):
        command = validate_and_classify({"messages": [MagicMock(content="hi")], **state})
    return command.goto


LIVE_ANCHOR = {"pending_suggestions": {"msg-1": {"proposal": PROPOSAL, "approved_by": []}}}


def test_nudge_with_a_pending_vote_acknowledges():
    assert _route("nudge", {"setup_stage": "content", **LIVE_ANCHOR}) == "acknowledge_pending"


def test_nudge_with_nothing_pending_follows_the_normal_path():
    assert _route("nudge", {"setup_stage": "content"}) == "provide_feedback"


def test_a_change_request_still_revises_even_with_a_vote_pending():
    """The summon that carries content must reach the feedback agent, not the pointer."""
    assert _route("feedback", {"setup_stage": "content", **LIVE_ANCHOR}) == "provide_feedback"


def test_a_real_question_still_gets_answered_with_a_vote_pending():
    assert _route("question", {"setup_stage": "content", **LIVE_ANCHOR}) == "search_documentation"


# --- parked details are spent once the labels they describe are approved ---

def test_design_notes_reducer_appends_and_clears():
    from src.agent.state import _append_notes

    assert _append_notes(["a"], ["b"]) == ["a", "b"]
    assert _append_notes(None, ["a"]) == ["a"]
    assert _append_notes(["a", "b"], None) == []  # None is the reset


@pytest.mark.asyncio
async def test_approving_labels_clears_the_parked_details():
    """They were carried from the purpose stage to be acted on here; once they have been, they
    would otherwise trail the group as context that still reads like an open request."""
    suggestions = {"msg-1": {"proposal": PROPOSAL, "approved_by": []}}

    with patch("src.agent.brainstorming.voting.graph") as mock_graph, \
         patch("src.agent.brainstorming.voting.commit_proposal", return_value={"labels": [{"identifier": "spam"}]}):
        mock_graph.get_state.return_value = _state(
            pending_suggestions=suggestions, setup_stage="content"
        )
        await process_approval_vote(CHANNEL_ID, "msg-1", "user-1", MAJORITY_THRESHOLD)

    payloads = [c.args[1] for c in mock_graph.update_state.call_args_list if len(c.args) > 1]
    commit = next(p for p in payloads if "labeler_config" in p)
    assert commit["setup_stage"] == "rules"
    assert commit["design_notes"] is None


# --- the visible half: marking the old card in Stream ---

@pytest.mark.asyncio
async def test_superseded_note_is_appended_to_the_old_message():
    from src.api.agent_runner import _mark_superseded_in_stream
    from src.api.messages import SUPERSEDED_NOTE

    client = MagicMock()
    client.get_message.return_value = {"message": {"text": "Here's the proposal"}}
    update = AsyncMock()

    with patch("src.api.agent_runner.get_stream_client", return_value=client), \
         patch("src.api.agent_runner._update_stream_message", update):
        await _mark_superseded_in_stream({"msg-old": {"superseded": True}})

    message_id, text = update.await_args.args
    assert message_id == "msg-old"
    assert text.startswith("Here's the proposal")
    assert SUPERSEDED_NOTE in text


@pytest.mark.asyncio
async def test_superseded_note_is_not_appended_twice():
    from src.api.agent_runner import _mark_superseded_in_stream
    from src.api.messages import SUPERSEDED_NOTE

    client = MagicMock()
    client.get_message.return_value = {"message": {"text": f"Proposal\n\n{SUPERSEDED_NOTE}"}}
    update = AsyncMock()

    with patch("src.api.agent_runner.get_stream_client", return_value=client), \
         patch("src.api.agent_runner._update_stream_message", update):
        await _mark_superseded_in_stream({"msg-old": {"superseded": True}})

    update.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_failed_edit_does_not_break_the_run():
    """The state guard is what makes old votes inert; the note is cosmetic and best-effort."""
    from src.api.agent_runner import _mark_superseded_in_stream

    client = MagicMock()
    client.get_message.side_effect = Exception("Stream down")

    with patch("src.api.agent_runner.get_stream_client", return_value=client):
        await _mark_superseded_in_stream({"msg-old": {"superseded": True}})  # must not raise
