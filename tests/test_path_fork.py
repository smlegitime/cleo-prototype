"""
Tests for the fork after the sandbox run: guide vs. going live.

Two anchors, one vote. Whichever reaches a majority first decides the path and closes the other,
so the losing card can't be tripped into weeks later from the scrollback. Choosing the guide
advances nothing — the labeler stays in the private sandbox at `deploy`.

Also covers the two rules that keep provisioning a conversation rather than an interrogation:
the governance capture runs only when CLEO is triggered, and the design agent is kept out of the
path entirely while the stage is `provision`.
"""

import os

os.environ.setdefault("STREAM_API_KEY", "test-key")
os.environ.setdefault("STREAM_API_SECRET", "test-secret")

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from langchain_core.messages import HumanMessage

from src.agent.brainstorming.voting import close_other_path, process_guide_choice
from src.api.helpers import asks_to_go_live

CHANNEL_ID = "channel-test"
GUIDE_ANCHOR = "msg-guide"
GO_LIVE_ANCHOR = "msg-go-live"


def _graph_with(**values) -> MagicMock:
    """A graph whose state holds `values` and records update_state calls."""
    mock_graph = MagicMock()
    state = MagicMock()
    state.values = {
        "lifecycle_stage": "deploy",
        "pending_guide_choice": {"message_id": GUIDE_ANCHOR, "approved_by": []},
        "pending_provision_approval": {"message_id": GO_LIVE_ANCHOR, "approved_by": []},
        **values,
    }
    mock_graph.get_state.return_value = state
    return mock_graph


def _updates(mock_graph: MagicMock) -> dict:
    """Every key written across all update_state calls, merged."""
    merged = {}
    for call in mock_graph.update_state.call_args_list:
        merged.update(call.args[1])
    return merged


# --- the guide vote ---

@pytest.mark.asyncio
async def test_a_majority_on_the_guide_anchor_carries_it():
    mock_graph = _graph_with()
    with patch("src.agent.brainstorming.voting.graph", mock_graph):
        carried = await process_guide_choice(CHANNEL_ID, GUIDE_ANCHOR, "user-0", 1)

    assert carried is True
    assert _updates(mock_graph)["pending_guide_choice"]["committed"] is True


@pytest.mark.asyncio
async def test_the_guide_path_advances_no_stage():
    """The whole point: the labeler stays in the private sandbox, so the channel stays at deploy."""
    mock_graph = _graph_with()
    with patch("src.agent.brainstorming.voting.graph", mock_graph):
        await process_guide_choice(CHANNEL_ID, GUIDE_ANCHOR, "user-0", 1)

    assert "lifecycle_stage" not in _updates(mock_graph)


@pytest.mark.asyncio
async def test_a_vote_short_of_the_threshold_records_but_does_not_carry():
    mock_graph = _graph_with()
    with patch("src.agent.brainstorming.voting.graph", mock_graph):
        carried = await process_guide_choice(CHANNEL_ID, GUIDE_ANCHOR, "user-0", 3)

    assert carried is False
    written = _updates(mock_graph)["pending_guide_choice"]
    assert written["approved_by"] == ["user-0"]
    assert "committed" not in written


@pytest.mark.asyncio
async def test_a_reaction_on_the_guide_anchor_after_the_group_moved_on_is_inert():
    """The anchor sits in the scroll forever; past `deploy` it must not re-answer a settled question."""
    mock_graph = _graph_with(lifecycle_stage="provision")
    with patch("src.agent.brainstorming.voting.graph", mock_graph):
        assert await process_guide_choice(CHANNEL_ID, GUIDE_ANCHOR, "user-0", 1) is False


@pytest.mark.asyncio
async def test_a_closed_guide_anchor_cannot_be_revoted():
    mock_graph = _graph_with(
        pending_guide_choice={"message_id": GUIDE_ANCHOR, "approved_by": [], "committed": True}
    )
    with patch("src.agent.brainstorming.voting.graph", mock_graph):
        assert await process_guide_choice(CHANNEL_ID, GUIDE_ANCHOR, "user-0", 1) is False


# --- closing the path not taken ---

@pytest.mark.asyncio
async def test_choosing_going_live_closes_the_guide_anchor():
    mock_graph = _graph_with()
    with patch("src.agent.brainstorming.voting.graph", mock_graph):
        closed = await close_other_path(CHANNEL_ID, "provision")

    assert closed == GUIDE_ANCHOR
    assert _updates(mock_graph)["pending_guide_choice"]["committed"] is True


@pytest.mark.asyncio
async def test_choosing_the_guide_closes_the_go_live_anchor():
    mock_graph = _graph_with()
    with patch("src.agent.brainstorming.voting.graph", mock_graph):
        closed = await close_other_path(CHANNEL_ID, "guide")

    assert closed == GO_LIVE_ANCHOR
    assert _updates(mock_graph)["pending_provision_approval"]["committed"] is True


@pytest.mark.asyncio
async def test_closing_an_already_closed_path_writes_nothing():
    """Idempotent: a second vote on the winning card must not re-close (and re-note) the loser."""
    mock_graph = _graph_with(
        pending_guide_choice={"message_id": GUIDE_ANCHOR, "approved_by": [], "committed": True}
    )
    with patch("src.agent.brainstorming.voting.graph", mock_graph):
        assert await close_other_path(CHANNEL_ID, "provision") is None

    mock_graph.update_state.assert_not_called()


@pytest.mark.asyncio
async def test_closing_is_safe_when_the_other_anchor_was_never_registered():
    """If Stream didn't return an id for one half, the other path still has to work."""
    mock_graph = _graph_with(pending_guide_choice=None)
    with patch("src.agent.brainstorming.voting.graph", mock_graph):
        assert await close_other_path(CHANNEL_ID, "provision") is None


@pytest.mark.asyncio
async def test_the_closed_card_is_greyed_out_and_says_why():
    from src.api.messages import GUIDE_PATH_CLOSED_NOTE
    from src.api.reporters import close_path_in_stream
    from src.api.stream import APPROVAL_SUPERSEDED

    note = GUIDE_PATH_CLOSED_NOTE.format(guide_url="https://app/?guide=dev3")
    client = MagicMock()
    client.get_message.return_value = {"message": {"text": "Option 1: keep it in the sandbox"}}
    update, tag = AsyncMock(), AsyncMock()

    with patch("src.api.reporters.get_stream_client", return_value=client), \
         patch("src.api.reporters._update_stream_message", update), \
         patch("src.api.reporters.set_approval_state", tag):
        await close_path_in_stream(GUIDE_ANCHOR, note)

    tag.assert_awaited_once_with(GUIDE_ANCHOR, APPROVAL_SUPERSEDED)
    # The guide needs no vote, so the closed card hands the link over rather than promising a
    # reopening that would have to be built.
    assert "https://app/?guide=dev3" in update.await_args.args[1]


@pytest.mark.asyncio
async def test_the_note_is_not_appended_twice():
    from src.api.messages import GO_LIVE_PATH_CLOSED_NOTE
    from src.api.reporters import close_path_in_stream

    client = MagicMock()
    client.get_message.return_value = {"message": {"text": f"Option 2\n\n{GO_LIVE_PATH_CLOSED_NOTE}"}}
    update = AsyncMock()

    with patch("src.api.reporters.get_stream_client", return_value=client), \
         patch("src.api.reporters._update_stream_message", update), \
         patch("src.api.reporters.set_approval_state", AsyncMock()):
        await close_path_in_stream(GUIDE_ANCHOR, GO_LIVE_PATH_CLOSED_NOTE)

    update.assert_not_awaited()


@pytest.mark.asyncio
async def test_nothing_to_close_is_a_no_op():
    from src.api.reporters import close_path_in_stream

    tag = AsyncMock()
    with patch("src.api.reporters.set_approval_state", tag):
        await close_path_in_stream(None, "note")

    tag.assert_not_awaited()


# --- the way back ---

@pytest.mark.parametrize("text", [
    "@CLEO go live",
    "cleo, go live",
    "cleo let's go live",
    "CLEO we want to go live",
    "cleo can we go live",
])
def test_asking_to_go_live_is_recognised(text):
    assert asks_to_go_live(text) is True


@pytest.mark.parametrize("text", [
    "what would going live involve?",
    "cleo what happens if we go live?",
    "we decided against going live",
    "we're not going live",
    "let's not go live",
    "is going live reversible?",
    "show me the guide",
    "",
])
def test_talking_about_going_live_is_not_asking_for_it(text):
    """A false positive reopens a vote nobody called for — worse than making them say it again."""
    assert asks_to_go_live(text) is False


@pytest.mark.asyncio
async def test_reopening_is_refused_when_an_anchor_is_already_open():
    """Two live go-live anchors would split the vote between them."""
    from src.api.reporters import _reopen_go_live_and_report

    mock_graph = _graph_with()   # pending_provision_approval is open, not committed
    post = AsyncMock()
    with patch("src.api.reporters.graph", mock_graph), \
         patch("src.api.reporters._post_path_anchor", post):
        await _reopen_go_live_and_report("messaging", CHANNEL_ID)

    post.assert_not_awaited()


@pytest.mark.asyncio
async def test_reopening_is_refused_outside_deploy():
    from src.api.reporters import _reopen_go_live_and_report

    mock_graph = _graph_with(lifecycle_stage="provision")
    post = AsyncMock()
    with patch("src.api.reporters.graph", mock_graph), \
         patch("src.api.reporters._post_path_anchor", post):
        await _reopen_go_live_and_report("messaging", CHANNEL_ID)

    post.assert_not_awaited()


@pytest.mark.asyncio
async def test_reopening_posts_a_fresh_anchor_after_the_guide_was_chosen():
    from src.api.reporters import _reopen_go_live_and_report

    mock_graph = _graph_with(
        pending_provision_approval={"message_id": GO_LIVE_ANCHOR, "approved_by": [], "committed": True},
        pending_guide_choice={"message_id": GUIDE_ANCHOR, "approved_by": ["user-0"], "committed": True},
    )
    post = AsyncMock(return_value="msg-new")
    with patch("src.api.reporters.graph", mock_graph), \
         patch("src.api.reporters.get_stream_client", MagicMock()), \
         patch("src.api.reporters._post_path_anchor", post):
        await _reopen_go_live_and_report("messaging", CHANNEL_ID)

    assert post.await_args.args[3] == "pending_provision_approval"


# --- provisioning stays a conversation ---

def test_the_design_agent_is_kept_out_of_provisioning():
    """The feedback agent is in reactive mode by now: left in the path it reads 'the custodian
    should be Maya' as a labeler change and stages a proposal nobody asked for."""
    from src.agent.brainstorming.nodes import validate_and_classify

    state = {
        "messages": [HumanMessage(content="the custodian should be Maya")],
        "setup_stage": "complete",
        "lifecycle_stage": "provision",
        "pending_suggestions": {},
        "pending_rule_suggestions": {},
    }
    with patch("src.agent.brainstorming.nodes._validate_and_classify_llm") as mock_llm:
        mock_llm.invoke.return_value = {
            "intent": "feedback", "atproto": "labeler", "topic": "labeler",
            "violation": False, "message": "",
        }
        cmd = validate_and_classify(state)

    assert cmd.goto == "draft_response"


def test_a_question_during_provisioning_is_still_answered():
    from src.agent.brainstorming.nodes import validate_and_classify

    state = {
        "messages": [HumanMessage(content="what is a custodian?")],
        "setup_stage": "complete",
        "lifecycle_stage": "provision",
        "pending_suggestions": {},
        "pending_rule_suggestions": {},
    }
    with patch("src.agent.brainstorming.nodes._validate_and_classify_llm") as mock_llm:
        mock_llm.invoke.return_value = {
            "intent": "question", "atproto": "labeler", "topic": "labeler",
            "violation": False, "message": "",
        }
        cmd = validate_and_classify(state)

    assert cmd.goto == "search_documentation"


def test_the_provisioning_stage_context_lists_what_is_left():
    from src.agent.brainstorming.nodes import _stage_context

    context = _stage_context({
        "setup_stage": "complete",
        "lifecycle_stage": "provision",
        "approvals_needed": 2,
        "governance": {"handle": "sourdough-standards", "custodian": None, "appeals": None},
        "pending_suggestions": {},
        "pending_rule_suggestions": {},
    })

    assert "handle = sourdough-standards" in context
    assert "Still to settle: custodian, appeals" in context


# --- the fork is actually posted ---

@pytest.mark.asyncio
async def test_the_sandbox_report_is_followed_by_both_path_anchors():
    """The entry point: one intro, then one anchor per path, each registered as its own vote."""
    from src.api import reporters

    channel = MagicMock()
    client = MagicMock()
    client.channel.return_value = channel
    post = AsyncMock(return_value="msg-x")

    with patch("src.api.reporters.get_stream_client", return_value=client), \
         patch("src.api.reporters.run_deploy_stage",
               AsyncMock(return_value={"status": "succeeded", "labels": 2, "rules": 3})), \
         patch("src.api.reporters.run_execute_stage",
               AsyncMock(return_value={"status": "succeeded", "did": "did:web:x",
                                       "total": 10, "records_emitted": 4, "per_label": {}})), \
         patch("src.api.reporters._set_ai_indicator", AsyncMock()), \
         patch("src.api.reporters._post_path_anchor", post):
        await reporters._run_deploy_and_report("messaging", CHANNEL_ID)

    registered = [call.args[3] for call in post.await_args_list]
    assert registered == ["pending_guide_choice", "pending_provision_approval"]


@pytest.mark.asyncio
async def test_a_failed_sandbox_run_posts_no_fork():
    """Nothing to choose between if the labeler didn't run."""
    from src.api import reporters

    client = MagicMock()
    client.channel.return_value = MagicMock()
    post = AsyncMock()

    with patch("src.api.reporters.get_stream_client", return_value=client), \
         patch("src.api.reporters.run_deploy_stage",
               AsyncMock(return_value={"status": "succeeded", "labels": 1, "rules": 1})), \
         patch("src.api.reporters.run_execute_stage",
               AsyncMock(return_value={"status": "failed", "did": None, "records_emitted": 0})), \
         patch("src.api.reporters._set_ai_indicator", AsyncMock()), \
         patch("src.api.reporters._post_path_anchor", post):
        await reporters._run_deploy_and_report("messaging", CHANNEL_ID)

    post.assert_not_awaited()


@pytest.mark.asyncio
async def test_an_anchor_stream_refuses_to_id_is_not_registered():
    """Registering a pending vote against a missing message id would make the OTHER path
    unwinnable — close_other_path would point at nothing."""
    from src.api.reporters import _post_path_anchor

    channel = MagicMock()
    channel.send_message.return_value = {"message": {}}   # no id
    mock_graph = _graph_with()

    with patch("src.api.reporters.graph", mock_graph):
        anchor_id = await _post_path_anchor(channel, CHANNEL_ID, "text", "pending_guide_choice")

    assert anchor_id is None
    mock_graph.update_state.assert_not_called()


def test_each_closed_card_carries_the_way_back_that_actually_exists():
    """The notes are not interchangeable: reopening going live needs a fresh vote (so that note
    names the phrase that triggers one), while the guide is just a link (so that note hands it
    over). Swapping them would promise a reopening nothing implements."""
    from src.api.messages import GO_LIVE_PATH_CLOSED_NOTE, GUIDE_PATH_CLOSED_NOTE

    guide_card = GUIDE_PATH_CLOSED_NOTE.format(guide_url="https://app/?guide=dev3")
    assert "https://app/?guide=dev3" in guide_card
    assert "go live" not in guide_card.lower().split("closed.")[1]

    assert "@CLEO go live" in GO_LIVE_PATH_CLOSED_NOTE
    assert "{guide_url}" not in GO_LIVE_PATH_CLOSED_NOTE


# =============================================================================
# The lifecycle-gate blind spot: every open vote is reportable, not just setup's
# =============================================================================

from src.agent.brainstorming.nodes import (
    _anchor_what_and_tally,
    _live_pending_anchor,
    _stage_context,
    acknowledge_pending,
)

PROPOSAL = {"display_name": "Test", "labels": []}


def _at(stage: str, **anchors) -> dict:
    """Channel state at a lifecycle stage with the named anchors open."""
    return {
        "setup_stage": "complete",
        "lifecycle_stage": stage,
        "approvals_needed": 2,
        "pending_suggestions": {},
        "pending_rule_suggestions": {},
        "pending_governance_suggestions": {},
        **anchors,
    }


@pytest.mark.parametrize("stage,key,kind,phrase", [
    ("preview", "pending_preview_approval", "preview", "sign-off on the preview"),
    ("generate", "pending_deploy_approval", "deploy", "build and test your labeler"),
    ("provision", "pending_provision_approval", "provision", "decision about going live"),
])
def test_each_lifecycle_gate_is_a_live_anchor(stage, key, kind, phrase):
    """These four gates were invisible: the scan only ever looked at the two setup stores, so a
    group parked on the preview, ship or go-live vote was told nothing was pending."""
    state = _at(stage, **{key: {"message_id": "m1", "approved_by": ["user-1"]}})

    anchor = _live_pending_anchor(state)
    assert anchor is not None
    assert anchor[0] == kind

    what, tally = _anchor_what_and_tally(state, anchor)
    assert phrase in what
    assert tally == "1 of 2 approvals so far"


def test_a_governance_confirm_card_is_a_live_anchor():
    state = _at("provision", pending_governance_suggestions={
        "m1": {"proposal": {"handle": "x"}, "approved_by": []},
    })

    kind, _ = _live_pending_anchor(state)
    assert kind == "governance"


def test_a_committed_gate_is_not_reported_as_waiting():
    state = _at("generate", pending_deploy_approval={
        "message_id": "m1", "approved_by": ["user-1", "user-2"], "committed": True,
    })

    assert _live_pending_anchor(state) is None


def test_the_current_gate_wins_over_an_older_uncommitted_one():
    """A proposal staged but never voted on shouldn't outrank the vote actually blocking progress."""
    state = _at(
        "generate",
        pending_deploy_approval={"message_id": "m-ship", "approved_by": []},
        pending_suggestions={"m-old": {"proposal": PROPOSAL, "approved_by": []}},
    )

    assert _live_pending_anchor(state)[0] == "deploy"


# --- the fork reads as one decision ---

def _forked(guide_votes=(), go_live_votes=()) -> dict:
    return _at(
        "deploy",
        pending_guide_choice={"message_id": GUIDE_ANCHOR, "approved_by": list(guide_votes)},
        pending_provision_approval={"message_id": GO_LIVE_ANCHOR, "approved_by": list(go_live_votes)},
    )


def test_both_fork_halves_open_reads_as_a_single_choice():
    """Reporting either half alone quotes a count for a vote the group isn't having."""
    kind, _ = _live_pending_anchor(_forked())
    assert kind == "fork"


def test_the_fork_tally_gives_both_counts():
    state = _forked(guide_votes=["user-1"])
    what, tally = _anchor_what_and_tally(state, _live_pending_anchor(state))

    assert what == "choice between the maintenance guide and going live"
    assert tally == "the guide has 1 of 2, going live has 0 of 2"


def test_an_untouched_fork_says_whichever_gets_there_first():
    state = _forked()
    _, tally = _anchor_what_and_tally(state, _live_pending_anchor(state))

    assert tally == "no votes either way yet — whichever gets 2 first decides"


def test_the_fork_tells_the_group_to_pick_one_not_to_approve_it():
    """'React 👍🏾 on it' is wrong when 'it' is two cards."""
    reply = acknowledge_pending(_forked())["draft_response"]

    assert "whichever of the two" in reply
    assert "React 👍🏾 on it to approve" not in reply


def test_a_closed_half_leaves_the_other_reported_on_its_own():
    """Once the group has chosen, the remaining card is a normal single vote again."""
    state = _forked(go_live_votes=["user-1"])
    state["pending_guide_choice"]["committed"] = True

    kind, _ = _live_pending_anchor(state)
    assert kind == "provision"


# --- what the group actually sees ---

def test_the_stage_context_no_longer_claims_nothing_is_pending_at_a_gate():
    """The check that started this: a channel parked on the ship gate used to be described to the
    drafting prompt as having nothing waiting — a positive claim, and wrong."""
    context = _stage_context(
        _at("generate", pending_deploy_approval={"message_id": "m1", "approved_by": ["user-1"]})
    )

    assert "Nothing is waiting on a vote right now." not in context
    assert "1 of 2 approvals so far" in context


def test_a_nudge_at_a_lifecycle_gate_points_at_the_vote():
    """Routing keys off _live_pending_anchor, so the gates were unreachable from a ping too."""
    from src.agent.brainstorming.nodes import validate_and_classify

    state = _at("generate", pending_deploy_approval={"message_id": "m1", "approved_by": []})
    state["messages"] = [HumanMessage(content="@CLEO?")]

    with patch("src.agent.brainstorming.nodes._validate_and_classify_llm") as mock_llm:
        mock_llm.invoke.return_value = {
            "intent": "nudge", "atproto": "labeler", "topic": "labeler",
            "violation": False, "message": "",
        }
        cmd = validate_and_classify(state)

    assert cmd.goto == "acknowledge_pending"
