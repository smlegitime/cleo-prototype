"""
Tests for majority voting logic in process_approval_vote.

Mocks graph.get_state / graph.update_state so no LangGraph state is touched.
"""

import pytest
from unittest.mock import MagicMock, patch, call

from src.agent.brainstorming.voting import (
    process_approval_vote, process_preview_approval, process_provision_approval,
    process_governance_approval, MAJORITY_THRESHOLD,
)


def _make_state(pending_suggestions: dict, labeler_config: dict = None, pending_rule_suggestions: dict = None,
                setup_stage: str = None, classification_rules: dict = None):
    mock_state = MagicMock()
    mock_state.values = {
        "pending_suggestions": pending_suggestions,
        "pending_rule_suggestions": pending_rule_suggestions or {},
        "labeler_config": labeler_config or {},
        "setup_stage": setup_stage,
        "classification_rules": classification_rules or {},
    }
    return mock_state


def _stage_written(mock_graph):
    """Return the setup_stage from the commit update_state call, or None if not written."""
    for c in mock_graph.update_state.call_args_list:
        payload = c.args[1] if len(c.args) > 1 else c.kwargs.get("values", {})
        if isinstance(payload, dict) and "setup_stage" in payload:
            return payload["setup_stage"]
    return "NOT_WRITTEN"


def _committed_flags(mock_graph):
    """Return every 'committed' value written back to a pending suggestion entry."""
    flags = []
    for c in mock_graph.update_state.call_args_list:
        payload = c.args[1] if len(c.args) > 1 else c.kwargs.get("values", {})
        if not isinstance(payload, dict):
            continue
        for key in ("pending_suggestions", "pending_rule_suggestions"):
            for entry in (payload.get(key) or {}).values():
                if "committed" in entry:
                    flags.append(entry["committed"])
    return flags


PROPOSAL = {"display_name": "Test Labeler", "description": "A test labeler", "labels": []}
RULES_PROPOSAL = {"spam": {"label_identifier": "spam", "include_signals": [], "exclude_signals": [], "notes": None}}
MESSAGE_ID = "msg-001"
CHANNEL_ID = "channel-test"


@pytest.mark.asyncio
async def test_unknown_message_id_returns_none():
    state = _make_state({})
    with patch("src.agent.brainstorming.voting.graph") as mock_graph:
        mock_graph.get_state.return_value = state
        result = await process_approval_vote(CHANNEL_ID, MESSAGE_ID, "user-1", 2)
    assert result is None


@pytest.mark.asyncio
async def test_single_approval_applies_in_small_channel():
    """Channel with <= MAJORITY_THRESHOLD members: 1 approval is enough."""
    suggestion = {"proposal": PROPOSAL, "approved_by": []}
    state = _make_state({MESSAGE_ID: suggestion})

    with patch("src.agent.brainstorming.voting.graph") as mock_graph, \
         patch("src.agent.brainstorming.voting.commit_proposal", return_value=PROPOSAL) as mock_commit:
        mock_graph.get_state.return_value = state
        result = await process_approval_vote(CHANNEL_ID, MESSAGE_ID, "user-1", MAJORITY_THRESHOLD)

    assert result == "proposal"
    mock_commit.assert_called_once_with(PROPOSAL, {})


@pytest.mark.asyncio
async def test_majority_required_in_large_channel():
    """Channel with > MAJORITY_THRESHOLD members: majority needed, 1 vote is not enough."""
    suggestion = {"proposal": PROPOSAL, "approved_by": []}
    state = _make_state({MESSAGE_ID: suggestion})
    non_ai_count = MAJORITY_THRESHOLD + 2  # e.g. 5

    with patch("src.agent.brainstorming.voting.graph") as mock_graph, \
         patch("src.agent.brainstorming.voting.commit_proposal") as mock_commit:
        mock_graph.get_state.return_value = state
        result = await process_approval_vote(CHANNEL_ID, MESSAGE_ID, "user-1", non_ai_count)

    assert result is None
    mock_commit.assert_not_called()


@pytest.mark.asyncio
async def test_majority_vote_applies_when_threshold_met():
    """Majority vote met in a large channel applies the proposal."""
    non_ai_count = MAJORITY_THRESHOLD + 2  # 5
    # 3 existing approvals already puts us over 50% of 5
    suggestion = {"proposal": PROPOSAL, "approved_by": ["user-1", "user-2", "user-3"]}
    state = _make_state({MESSAGE_ID: suggestion})

    with patch("src.agent.brainstorming.voting.graph") as mock_graph, \
         patch("src.agent.brainstorming.voting.commit_proposal", return_value=PROPOSAL) as mock_commit:
        mock_graph.get_state.return_value = state
        result = await process_approval_vote(CHANNEL_ID, MESSAGE_ID, "user-4", non_ai_count)

    assert result == "proposal"
    mock_commit.assert_called_once()


@pytest.mark.asyncio
async def test_duplicate_vote_not_counted_twice():
    """The same user voting twice should not increase the approval count."""
    suggestion = {"proposal": PROPOSAL, "approved_by": ["user-1"]}
    state = _make_state({MESSAGE_ID: suggestion})
    non_ai_count = MAJORITY_THRESHOLD + 2  # 5, needs >2.5 = 3 approvals

    with patch("src.agent.brainstorming.voting.graph") as mock_graph, \
         patch("src.agent.brainstorming.voting.commit_proposal") as mock_commit:
        mock_graph.get_state.return_value = state
        # user-1 votes again — still only 1 unique approval
        result = await process_approval_vote(CHANNEL_ID, MESSAGE_ID, "user-1", non_ai_count)

    assert result is None
    mock_commit.assert_not_called()


@pytest.mark.asyncio
async def test_proposal_merged_with_existing_config():
    """commit_proposal receives the current labeler_config from state."""
    existing_config = {"display_name": "Old Name", "labels": []}
    suggestion = {"proposal": PROPOSAL, "approved_by": []}
    state = _make_state({MESSAGE_ID: suggestion}, labeler_config=existing_config)

    with patch("src.agent.brainstorming.voting.graph") as mock_graph, \
         patch("src.agent.brainstorming.voting.commit_proposal", return_value={**existing_config, **PROPOSAL}) as mock_commit:
        mock_graph.get_state.return_value = state
        await process_approval_vote(CHANNEL_ID, MESSAGE_ID, "user-1", MAJORITY_THRESHOLD)

    mock_commit.assert_called_once_with(PROPOSAL, existing_config)


@pytest.mark.asyncio
async def test_rule_suggestion_approval_commits_rules_not_proposal():
    """A message_id staged under pending_rule_suggestions is voted on independently
    of pending_suggestions, and commits via commit_rules into classification_rules."""
    suggestion = {"proposal": RULES_PROPOSAL, "approved_by": []}
    state = _make_state({}, pending_rule_suggestions={MESSAGE_ID: suggestion})

    with patch("src.agent.brainstorming.voting.graph") as mock_graph, \
         patch("src.agent.brainstorming.voting.commit_rules", return_value=RULES_PROPOSAL) as mock_commit_rules, \
         patch("src.agent.brainstorming.voting.commit_proposal") as mock_commit_proposal:
        mock_graph.get_state.return_value = state
        result = await process_approval_vote(CHANNEL_ID, MESSAGE_ID, "user-1", MAJORITY_THRESHOLD)

    assert result == "rules"
    mock_commit_rules.assert_called_once_with(RULES_PROPOSAL, {})
    mock_commit_proposal.assert_not_called()


# =============================================================================
# Idempotency: an already-committed suggestion must not re-commit or re-announce
# =============================================================================

@pytest.mark.asyncio
async def test_committed_suggestion_is_a_noop():
    """A second approval reaction on an already-committed proposal returns None and
    does not commit again (prevents duplicate 'Proposal approved!' messages)."""
    suggestion = {"proposal": PROPOSAL, "approved_by": ["user-1"], "committed": True}
    state = _make_state({MESSAGE_ID: suggestion})

    with patch("src.agent.brainstorming.voting.graph") as mock_graph, \
         patch("src.agent.brainstorming.voting.commit_proposal") as mock_commit:
        mock_graph.get_state.return_value = state
        result = await process_approval_vote(CHANNEL_ID, MESSAGE_ID, "user-2", MAJORITY_THRESHOLD)

    assert result is None
    mock_commit.assert_not_called()
    mock_graph.update_state.assert_not_called()


@pytest.mark.asyncio
async def test_commit_marks_suggestion_committed():
    """A successful commit writes committed=True back to the pending suggestion."""
    suggestion = {"proposal": PROPOSAL, "approved_by": []}
    state = _make_state({MESSAGE_ID: suggestion})

    with patch("src.agent.brainstorming.voting.graph") as mock_graph, \
         patch("src.agent.brainstorming.voting.commit_proposal", return_value=PROPOSAL):
        mock_graph.get_state.return_value = state
        result = await process_approval_vote(CHANNEL_ID, MESSAGE_ID, "user-1", MAJORITY_THRESHOLD)

    assert result == "proposal"
    assert _committed_flags(mock_graph) == [True]


# =============================================================================
# Approval advances setup_stage (moved out of provide_feedback's draft-time path)
# =============================================================================

@pytest.mark.asyncio
async def test_proposal_approval_advances_content_to_rules():
    labeled_config = {"display_name": "Guard", "description": "d", "labels": [{"identifier": "spam"}]}
    suggestion = {"proposal": labeled_config, "approved_by": []}
    state = _make_state({MESSAGE_ID: suggestion}, setup_stage="content")

    with patch("src.agent.brainstorming.voting.graph") as mock_graph, \
         patch("src.agent.brainstorming.voting.commit_proposal", return_value=labeled_config):
        mock_graph.get_state.return_value = state
        await process_approval_vote(CHANNEL_ID, MESSAGE_ID, "user-1", MAJORITY_THRESHOLD)

    assert _stage_written(mock_graph) == "rules"


@pytest.mark.asyncio
async def test_combined_proposal_approval_cascades_purpose_to_rules():
    """A single proposal carrying name + description + labels, approved while still in
    'purpose', must cascade past 'content' all the way to 'rules'."""
    combined = {"display_name": "Guard", "description": "d", "labels": [{"identifier": "spam"}]}
    suggestion = {"proposal": combined, "approved_by": []}
    state = _make_state({MESSAGE_ID: suggestion}, setup_stage="purpose")

    with patch("src.agent.brainstorming.voting.graph") as mock_graph, \
         patch("src.agent.brainstorming.voting.commit_proposal", return_value=combined):
        mock_graph.get_state.return_value = state
        await process_approval_vote(CHANNEL_ID, MESSAGE_ID, "user-1", MAJORITY_THRESHOLD)

    assert _stage_written(mock_graph) == "rules"


@pytest.mark.asyncio
async def test_rules_approval_advances_rules_to_complete():
    suggestion = {"proposal": RULES_PROPOSAL, "approved_by": []}
    state = _make_state(
        {}, labeler_config={"labels": [{"identifier": "spam"}]},
        pending_rule_suggestions={MESSAGE_ID: suggestion}, setup_stage="rules",
    )

    with patch("src.agent.brainstorming.voting.graph") as mock_graph, \
         patch("src.agent.brainstorming.voting.commit_rules", return_value=RULES_PROPOSAL):
        mock_graph.get_state.return_value = state
        await process_approval_vote(CHANNEL_ID, MESSAGE_ID, "user-1", MAJORITY_THRESHOLD)

    assert _stage_written(mock_graph) == "complete"


def _lifecycle_written(mock_graph):
    """Return (stage, status) written to the lifecycle fields, or 'NOT_WRITTEN' if untouched."""
    for c in mock_graph.update_state.call_args_list:
        payload = c.args[1] if len(c.args) > 1 else c.kwargs.get("values", {})
        if isinstance(payload, dict) and "lifecycle_stage" in payload:
            return (payload["lifecycle_stage"], payload.get("lifecycle_status"))
    return "NOT_WRITTEN"


def _spec_id_written(mock_graph):
    """Return the spec_id written to state, or 'NOT_WRITTEN' if none was."""
    for c in mock_graph.update_state.call_args_list:
        payload = c.args[1] if len(c.args) > 1 else c.kwargs.get("values", {})
        if isinstance(payload, dict) and "spec_id" in payload:
            return payload["spec_id"]
    return "NOT_WRITTEN"


@pytest.mark.asyncio
async def test_rules_approval_opens_preview_lifecycle():
    """Completing setup via a rules approval hands off into the lifecycle at 'preview'."""
    suggestion = {"proposal": RULES_PROPOSAL, "approved_by": []}
    state = _make_state(
        {}, labeler_config={"labels": [{"identifier": "spam"}]},
        pending_rule_suggestions={MESSAGE_ID: suggestion}, setup_stage="rules",
    )

    with patch("src.agent.brainstorming.voting.graph") as mock_graph, \
         patch("src.agent.brainstorming.voting.commit_rules", return_value=RULES_PROPOSAL):
        mock_graph.get_state.return_value = state
        await process_approval_vote(CHANNEL_ID, MESSAGE_ID, "user-1", MAJORITY_THRESHOLD)

    assert _stage_written(mock_graph) == "complete"
    assert _lifecycle_written(mock_graph) == ("preview", "pending")


@pytest.mark.asyncio
async def test_preview_handoff_records_spec_id():
    """The handoff serializes the approved design and stores its content hash as spec_id."""
    from src.agent.spec import build_spec

    labeler_config = {"labels": [{"identifier": "spam"}]}
    suggestion = {"proposal": RULES_PROPOSAL, "approved_by": []}
    state = _make_state(
        {}, labeler_config=labeler_config,
        pending_rule_suggestions={MESSAGE_ID: suggestion}, setup_stage="rules",
    )

    with patch("src.agent.brainstorming.voting.graph") as mock_graph, \
         patch("src.agent.brainstorming.voting.commit_rules", return_value=RULES_PROPOSAL):
        mock_graph.get_state.return_value = state
        await process_approval_vote(CHANNEL_ID, MESSAGE_ID, "user-1", MAJORITY_THRESHOLD)

    written = _spec_id_written(mock_graph)
    expected = build_spec(labeler_config, RULES_PROPOSAL)["spec_id"]
    assert written.startswith("sha256:")
    assert written == expected


@pytest.mark.asyncio
async def test_rule_edit_during_maintenance_refreshes_spec_id_without_resetting_lifecycle():
    """A rule edit once the labeler is live refreshes spec_id (design changed) but must not
    knock the lifecycle back to preview."""
    from src.agent.spec import build_spec

    labeler_config = {"labels": [{"identifier": "spam"}]}
    suggestion = {"proposal": RULES_PROPOSAL, "approved_by": []}
    state = _make_state(
        {}, labeler_config=labeler_config,
        pending_rule_suggestions={MESSAGE_ID: suggestion}, setup_stage="complete",
        classification_rules={"spam": {}},
    )
    # Labeler already deployed and running.
    state.values["lifecycle_stage"] = "live"

    with patch("src.agent.brainstorming.voting.graph") as mock_graph, \
         patch("src.agent.brainstorming.voting.commit_rules", return_value=RULES_PROPOSAL):
        mock_graph.get_state.return_value = state
        await process_approval_vote(CHANNEL_ID, MESSAGE_ID, "user-1", MAJORITY_THRESHOLD)

    assert _lifecycle_written(mock_graph) == "NOT_WRITTEN"
    assert _spec_id_written(mock_graph) == build_spec(labeler_config, RULES_PROPOSAL)["spec_id"]


@pytest.mark.asyncio
async def test_config_edit_during_lifecycle_refreshes_spec_id():
    """A label edit while in preview refreshes spec_id, even though it's the proposal branch."""
    from src.agent.spec import build_spec

    merged_config = {"labels": [{"identifier": "spam"}, {"identifier": "scam"}]}
    rules = {"spam": {}}
    suggestion = {"proposal": PROPOSAL, "approved_by": []}
    state = _make_state(
        {MESSAGE_ID: suggestion}, setup_stage="complete", classification_rules=rules,
    )
    state.values["lifecycle_stage"] = "preview"

    with patch("src.agent.brainstorming.voting.graph") as mock_graph, \
         patch("src.agent.brainstorming.voting.commit_proposal", return_value=merged_config):
        mock_graph.get_state.return_value = state
        await process_approval_vote(CHANNEL_ID, MESSAGE_ID, "user-1", MAJORITY_THRESHOLD)

    assert _spec_id_written(mock_graph) == build_spec(merged_config, rules)["spec_id"]


@pytest.mark.asyncio
async def test_config_approval_during_setup_writes_no_spec_id():
    """Before the lifecycle starts, a config approval must not stamp spec_id (nothing consumes it)."""
    suggestion = {"proposal": PROPOSAL, "approved_by": []}
    state = _make_state({MESSAGE_ID: suggestion}, setup_stage="content")  # lifecycle_stage absent -> None

    with patch("src.agent.brainstorming.voting.graph") as mock_graph, \
         patch("src.agent.brainstorming.voting.commit_proposal", return_value=PROPOSAL):
        mock_graph.get_state.return_value = state
        await process_approval_vote(CHANNEL_ID, MESSAGE_ID, "user-1", MAJORITY_THRESHOLD)

    assert _spec_id_written(mock_graph) == "NOT_WRITTEN"


@pytest.mark.asyncio
async def test_stage_advance_does_not_run_before_threshold_met():
    """No commit, no stage write, when the majority threshold isn't met."""
    suggestion = {"proposal": PROPOSAL, "approved_by": []}
    state = _make_state({MESSAGE_ID: suggestion}, setup_stage="content")
    non_ai_count = MAJORITY_THRESHOLD + 2

    with patch("src.agent.brainstorming.voting.graph") as mock_graph, \
         patch("src.agent.brainstorming.voting.commit_proposal"):
        mock_graph.get_state.return_value = state
        result = await process_approval_vote(CHANNEL_ID, MESSAGE_ID, "user-1", non_ai_count)

    assert result is None
    assert _stage_written(mock_graph) == "NOT_WRITTEN"


def _feedback_markers(mock_graph):
    """Return the content of every message appended to feedback_messages."""
    out = []
    for c in mock_graph.update_state.call_args_list:
        payload = c.args[1] if len(c.args) > 1 else c.kwargs.get("values", {})
        if isinstance(payload, dict):
            for m in payload.get("feedback_messages") or []:
                out.append(m.content)
    return out


@pytest.mark.asyncio
async def test_approved_proposal_is_recorded_in_the_feedback_history():
    """The feedback agent decides from feedback_messages, not labeler_config. Committing
    without recording the vote there leaves its own 'staged for group approval' claim
    uncontradicted, so it asks for an approval it already has — on a message that is now
    committed and inert, which the group cannot react its way out of.
    """
    suggestion = {"proposal": PROPOSAL, "approved_by": []}
    state = _make_state({MESSAGE_ID: suggestion}, setup_stage="content")

    with patch("src.agent.brainstorming.voting.graph") as mock_graph, \
         patch("src.agent.brainstorming.voting.commit_proposal", return_value=PROPOSAL):
        mock_graph.get_state.return_value = state
        await process_approval_vote(CHANNEL_ID, MESSAGE_ID, "user-1", 2)

    markers = _feedback_markers(mock_graph)
    assert len(markers) == 1
    assert "APPROVED" in markers[0]
    assert "labeler configuration" in markers[0]
    assert "do not ask the group to approve them again" in markers[0]


@pytest.mark.asyncio
async def test_approved_rules_are_recorded_in_the_feedback_history():
    suggestion = {"proposal": RULES_PROPOSAL, "approved_by": []}
    state = _make_state({}, pending_rule_suggestions={MESSAGE_ID: suggestion}, setup_stage="rules")

    with patch("src.agent.brainstorming.voting.graph") as mock_graph, \
         patch("src.agent.brainstorming.voting.commit_rules", return_value=RULES_PROPOSAL):
        mock_graph.get_state.return_value = state
        await process_approval_vote(CHANNEL_ID, MESSAGE_ID, "user-1", 2)

    markers = _feedback_markers(mock_graph)
    assert len(markers) == 1
    assert "classification rules" in markers[0]


@pytest.mark.asyncio
async def test_no_approval_marker_before_threshold_met():
    """An un-approved proposal must not tell the agent it was approved."""
    suggestion = {"proposal": PROPOSAL, "approved_by": []}
    state = _make_state({MESSAGE_ID: suggestion}, setup_stage="content")

    with patch("src.agent.brainstorming.voting.graph") as mock_graph, \
         patch("src.agent.brainstorming.voting.commit_proposal"):
        mock_graph.get_state.return_value = state
        await process_approval_vote(CHANNEL_ID, MESSAGE_ID, "user-1", MAJORITY_THRESHOLD + 2)

    assert _feedback_markers(mock_graph) == []


# ---- process_preview_approval (advances lifecycle preview -> generate) ----

PREVIEW_MSG = "preview-approval-msg"


def _preview_state(pending_preview_approval, lifecycle_stage="preview"):
    mock_state = MagicMock()
    mock_state.values = {
        "pending_preview_approval": pending_preview_approval,
        "lifecycle_stage": lifecycle_stage,
    }
    return mock_state


def _preview_written(mock_graph):
    """Return the last pending_preview_approval payload written, or None."""
    out = None
    for c in mock_graph.update_state.call_args_list:
        payload = c.args[1] if len(c.args) > 1 else c.kwargs.get("values", {})
        if isinstance(payload, dict) and "pending_preview_approval" in payload:
            out = payload["pending_preview_approval"]
    return out


@pytest.mark.asyncio
async def test_preview_approval_advances_to_generate():
    state = _preview_state({"message_id": PREVIEW_MSG, "approved_by": []})
    with patch("src.agent.brainstorming.voting.graph") as mock_graph:
        mock_graph.get_state.return_value = state
        advanced = await process_preview_approval(CHANNEL_ID, PREVIEW_MSG, "user-1", MAJORITY_THRESHOLD)

    assert advanced is True
    # Preview approval kicks off bundle generation (sandbox path); provision runs later, not here.
    assert _lifecycle_written(mock_graph) == ("generate", "pending")
    assert _preview_written(mock_graph)["committed"] is True


@pytest.mark.asyncio
async def test_preview_approval_below_threshold_records_vote_only():
    state = _preview_state({"message_id": PREVIEW_MSG, "approved_by": []})
    non_ai_count = MAJORITY_THRESHOLD + 2  # needs a majority, one vote isn't enough
    with patch("src.agent.brainstorming.voting.graph") as mock_graph:
        mock_graph.get_state.return_value = state
        advanced = await process_preview_approval(CHANNEL_ID, PREVIEW_MSG, "user-1", non_ai_count)

    assert advanced is False
    assert _lifecycle_written(mock_graph) == "NOT_WRITTEN"
    assert _preview_written(mock_graph)["approved_by"] == ["user-1"]


@pytest.mark.asyncio
async def test_preview_approval_ignores_non_anchor_message():
    state = _preview_state({"message_id": PREVIEW_MSG, "approved_by": []})
    with patch("src.agent.brainstorming.voting.graph") as mock_graph:
        mock_graph.get_state.return_value = state
        advanced = await process_preview_approval(CHANNEL_ID, "some-other-msg", "user-1", MAJORITY_THRESHOLD)

    assert advanced is False
    mock_graph.update_state.assert_not_called()


@pytest.mark.asyncio
async def test_preview_approval_noop_once_committed():
    state = _preview_state({"message_id": PREVIEW_MSG, "approved_by": ["user-1"], "committed": True})
    with patch("src.agent.brainstorming.voting.graph") as mock_graph:
        mock_graph.get_state.return_value = state
        advanced = await process_preview_approval(CHANNEL_ID, PREVIEW_MSG, "user-2", MAJORITY_THRESHOLD)

    assert advanced is False
    mock_graph.update_state.assert_not_called()


@pytest.mark.asyncio
async def test_preview_approval_ignored_when_not_in_preview():
    state = _preview_state({"message_id": PREVIEW_MSG, "approved_by": []}, lifecycle_stage="provision")
    with patch("src.agent.brainstorming.voting.graph") as mock_graph:
        mock_graph.get_state.return_value = state
        advanced = await process_preview_approval(CHANNEL_ID, PREVIEW_MSG, "user-1", MAJORITY_THRESHOLD)

    assert advanced is False
    mock_graph.update_state.assert_not_called()


# ---- Go-live gate (deploy -> provision) and the governance confirm card ----

GOLIVE_MSG = "msg-golive"
GOV_MSG = "msg-governance"


def _provision_state(pending, lifecycle_stage="deploy"):
    mock_state = MagicMock()
    mock_state.values = {
        "pending_provision_approval": pending,
        "lifecycle_stage": lifecycle_stage,
    }
    return mock_state


def _governance_state(suggestions, governance=None, lifecycle_stage="provision"):
    mock_state = MagicMock()
    mock_state.values = {
        "pending_governance_suggestions": suggestions,
        "governance": governance or {},
        "lifecycle_stage": lifecycle_stage,
    }
    return mock_state


def _payloads(mock_graph, key):
    """Every payload written under `key`, in order."""
    out = []
    for c in mock_graph.update_state.call_args_list:
        payload = c.args[1] if len(c.args) > 1 else c.kwargs.get("values", {})
        if isinstance(payload, dict) and key in payload:
            out.append(payload[key])
    return out


@pytest.mark.asyncio
async def test_golive_approval_advances_deploy_to_provision():
    state = _provision_state({"message_id": GOLIVE_MSG, "approved_by": []})
    with patch("src.agent.brainstorming.voting.graph") as mock_graph:
        mock_graph.get_state.return_value = state
        advanced = await process_provision_approval(CHANNEL_ID, GOLIVE_MSG, "user-1", MAJORITY_THRESHOLD)

    assert advanced is True
    assert _lifecycle_written(mock_graph) == ("provision", "pending")
    assert _payloads(mock_graph, "pending_provision_approval")[-1]["committed"] is True


@pytest.mark.asyncio
async def test_golive_approval_below_threshold_records_vote_only():
    state = _provision_state({"message_id": GOLIVE_MSG, "approved_by": []})
    with patch("src.agent.brainstorming.voting.graph") as mock_graph:
        mock_graph.get_state.return_value = state
        advanced = await process_provision_approval(
            CHANNEL_ID, GOLIVE_MSG, "user-1", MAJORITY_THRESHOLD + 2
        )

    assert advanced is False
    assert _lifecycle_written(mock_graph) == "NOT_WRITTEN"
    assert _payloads(mock_graph, "pending_provision_approval")[-1]["approved_by"] == ["user-1"]


@pytest.mark.asyncio
async def test_golive_approval_ignored_outside_deploy():
    state = _provision_state({"message_id": GOLIVE_MSG, "approved_by": []}, lifecycle_stage="preview")
    with patch("src.agent.brainstorming.voting.graph") as mock_graph:
        mock_graph.get_state.return_value = state
        advanced = await process_provision_approval(CHANNEL_ID, GOLIVE_MSG, "user-1", MAJORITY_THRESHOLD)

    assert advanced is False
    mock_graph.update_state.assert_not_called()


@pytest.mark.asyncio
async def test_governance_approval_commits_and_marks_complete():
    proposal = {
        "handle_choice": "wellness-watch.bsky.social",
        "custodian_display_name": "Ama",
        "appeals_contact": "the mod team",
    }
    state = _governance_state({GOV_MSG: {"proposal": proposal, "approved_by": []}})
    with patch("src.agent.brainstorming.voting.graph") as mock_graph:
        mock_graph.get_state.return_value = state
        merged = await process_governance_approval(CHANNEL_ID, GOV_MSG, "user-1", MAJORITY_THRESHOLD)

    assert merged["custodian_display_name"] == "Ama"
    assert merged["hosting_tier"] == "hosted"
    assert _payloads(mock_graph, "pending_governance_suggestions")[-1][GOV_MSG]["committed"] is True
    # All three answered -> the stage's work is done, so status succeeds...
    assert _payloads(mock_graph, "lifecycle_status")[-1] == "succeeded"
    # ...but the stage must NOT advance to 'live': creating the account needs the email addresses
    # and a step that isn't built. Nothing may write lifecycle_stage here.
    assert _payloads(mock_graph, "lifecycle_stage") == []


@pytest.mark.asyncio
async def test_governance_approval_stays_in_progress_while_answers_are_missing():
    state = _governance_state({GOV_MSG: {"proposal": {"custodian_display_name": "Ama"}, "approved_by": []}})
    with patch("src.agent.brainstorming.voting.graph") as mock_graph:
        mock_graph.get_state.return_value = state
        merged = await process_governance_approval(CHANNEL_ID, GOV_MSG, "user-1", MAJORITY_THRESHOLD)

    assert merged["custodian_display_name"] == "Ama"
    assert _payloads(mock_graph, "lifecycle_status")[-1] == "in_progress"


@pytest.mark.asyncio
async def test_governance_approval_merges_over_earlier_answers():
    """A second card must not wipe answers committed by the first."""
    state = _governance_state(
        {GOV_MSG: {"proposal": {"appeals_contact": "the mod team"}, "approved_by": []}},
        governance={"handle_choice": "wellness-watch.bsky.social"},
    )
    with patch("src.agent.brainstorming.voting.graph") as mock_graph:
        mock_graph.get_state.return_value = state
        merged = await process_governance_approval(CHANNEL_ID, GOV_MSG, "user-1", MAJORITY_THRESHOLD)

    assert merged["handle_choice"] == "wellness-watch.bsky.social"
    assert merged["appeals_contact"] == "the mod team"


@pytest.mark.asyncio
async def test_governance_approval_noop_once_committed():
    state = _governance_state(
        {GOV_MSG: {"proposal": {"custodian_display_name": "Ama"}, "approved_by": ["u1"], "committed": True}}
    )
    with patch("src.agent.brainstorming.voting.graph") as mock_graph:
        mock_graph.get_state.return_value = state
        merged = await process_governance_approval(CHANNEL_ID, GOV_MSG, "user-2", MAJORITY_THRESHOLD)

    assert merged is None
    mock_graph.update_state.assert_not_called()


@pytest.mark.asyncio
async def test_governance_approval_ignored_outside_provision():
    state = _governance_state(
        {GOV_MSG: {"proposal": {"custodian_display_name": "Ama"}, "approved_by": []}},
        lifecycle_stage="deploy",
    )
    with patch("src.agent.brainstorming.voting.graph") as mock_graph:
        mock_graph.get_state.return_value = state
        merged = await process_governance_approval(CHANNEL_ID, GOV_MSG, "user-1", MAJORITY_THRESHOLD)

    assert merged is None
    mock_graph.update_state.assert_not_called()
