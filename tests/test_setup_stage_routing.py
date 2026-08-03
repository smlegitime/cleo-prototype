"""
Tests for the guided setup-stage flow (setup_stage: purpose -> content -> rules -> complete).

Mocks all LLM calls so tests run without API keys and are deterministic. Covers three
layers: the router's setup_stage gate (validate_and_classify), the feedback agent's per-stage
prompt selection (call_model), and how provide_feedback integrates setup_stage into the feedback
subgraph.
"""

from typing import get_args
from unittest.mock import patch

import pytest
from langchain_core.messages import HumanMessage, AIMessage

from src.agent.brainstorming.nodes import (
    PURPOSE_MAX_TURNS, PURPOSE_QUESTION, validate_and_classify, provide_feedback, draft_response,
    _unconsumed_human_messages,
)
from src.agent.state import BrainstormingAgentState, ClassificationSignal
from src.agent.feedback.nodes import call_model
from src.agent.feedback.state import FeedbackGraphState
from src.agent.feedback.tools import SignalInput
from src.agent.prompts import RULES_DERIVATION_PROMPT


def _state(message: str, setup_stage: str | None = None,
           feedback_messages: list | None = None) -> BrainstormingAgentState:
    return {
        "messages": [HumanMessage(content=message)],
        "labeler_config": {},
        "validation": None,
        "classification": None,
        "search_results": None,
        "conversation_summary": None,
        "draft_response": None,
        "feedback_response": None,
        "feedback_messages": feedback_messages if feedback_messages is not None else [],
        "reactions": [],
        "pending_proposal": None,
        "pending_suggestions": {},
        "setup_stage": setup_stage,
    }


def _llm_result(intent: str, atproto: str = "bluesky", violation: bool = False, message: str = ""):
    return {"intent": intent, "atproto": atproto, "topic": atproto, "violation": violation, "message": message}


# =============================================================================
# validate_and_classify: setup_stage gating precedence
# =============================================================================

VALIDATE_PATCH_TARGET = "src.agent.brainstorming.nodes._validate_and_classify_llm"


def test_summary_intent_survives_purpose_stage():
    """Summary is read-only and doesn't touch the config being built, so it is
    checked before the setup_stage gate rather than being absorbed by it."""
    with patch(VALIDATE_PATCH_TARGET) as mock_llm:
        mock_llm.invoke.return_value = _llm_result("summary")
        cmd = validate_and_classify(_state("what have we discussed", setup_stage="purpose"))
    assert cmd.goto == "summarize_conversation"


def test_content_stage_overrides_show_config_intent():
    with patch(VALIDATE_PATCH_TARGET) as mock_llm:
        mock_llm.invoke.return_value = _llm_result("show_config")
        cmd = validate_and_classify(_state("show my config", setup_stage="content"))
    assert cmd.goto == "provide_feedback"


def test_rules_stage_overrides_generate_code_intent():
    with patch(VALIDATE_PATCH_TARGET) as mock_llm:
        mock_llm.invoke.return_value = _llm_result("generate_code")
        cmd = validate_and_classify(_state("write the code", setup_stage="rules"))
    assert cmd.goto == "provide_feedback"


def test_complete_stage_does_not_override_summary_intent():
    with patch(VALIDATE_PATCH_TARGET) as mock_llm:
        mock_llm.invoke.return_value = _llm_result("summary")
        cmd = validate_and_classify(_state("what have we discussed", setup_stage="complete"))
    assert cmd.goto == "summarize_conversation"


def test_no_setup_stage_does_not_override_summary_intent():
    with patch(VALIDATE_PATCH_TARGET) as mock_llm:
        mock_llm.invoke.return_value = _llm_result("summary")
        cmd = validate_and_classify(_state("what have we discussed", setup_stage=None))
    assert cmd.goto == "summarize_conversation"


def test_documentation_question_still_wins_over_setup_stage_gate():
    with patch(VALIDATE_PATCH_TARGET) as mock_llm:
        mock_llm.invoke.return_value = _llm_result("question", "labeler")
        cmd = validate_and_classify(_state("what is a labeler?", setup_stage="purpose"))
    assert cmd.goto == "search_documentation"


def test_violation_still_overrides_setup_stage_gate():
    with patch(VALIDATE_PATCH_TARGET) as mock_llm:
        mock_llm.invoke.return_value = _llm_result("feedback", violation=True, message="bad content")
        cmd = validate_and_classify(_state("some violating message", setup_stage="content"))
    assert cmd.goto == "draft_response"


# =============================================================================
# call_model: per-stage prompt selection (src/agent/feedback/nodes.py)
# =============================================================================

CALL_MODEL_PATCH_TARGET = "src.agent.feedback.nodes.tools_model"


def _feedback_state(message: str, setup_stage: str | None = None, system_prompt: str | None = None) -> FeedbackGraphState:
    state = {
        "messages": [HumanMessage(content=message)],
        "labels": {},
        "labeler_config": {},
        "system_prompt": system_prompt,
    }
    if setup_stage is not None:
        state["setup_stage"] = setup_stage
    return state


def _sent_system_prompt(mock_model) -> str:
    sent_messages = mock_model.invoke.call_args[0][0]
    return sent_messages[0]["content"]


def test_purpose_stage_marker_reaches_the_prompt():
    with patch(CALL_MODEL_PATCH_TARGET) as mock_model:
        mock_model.invoke.return_value = AIMessage(content="what's this labeler for?")
        call_model(_feedback_state("we want a labeler", setup_stage="purpose"))
    assert "Setup stage: purpose" in _sent_system_prompt(mock_model)


def test_content_stage_marker_reaches_the_prompt():
    with patch(CALL_MODEL_PATCH_TARGET) as mock_model:
        mock_model.invoke.return_value = AIMessage(content="what should each label catch?")
        call_model(_feedback_state("keep out spam", setup_stage="content"))
    assert "Setup stage: content" in _sent_system_prompt(mock_model)


def test_stage_directive_precedes_the_label_reference():
    """Ordering is load-bearing: with the label mechanics first, a group already deep in label
    talk pulls the model into building them while the stage is still 'purpose'."""
    with patch(CALL_MODEL_PATCH_TARGET) as mock_model:
        mock_model.invoke.return_value = AIMessage(content="...")
        call_model(_feedback_state("we want a labeler", setup_stage="purpose"))
    prompt = _sent_system_prompt(mock_model)
    assert prompt.index("What to do at this stage") < prompt.index("Reference: how to build a label")


def test_purpose_prompt_states_what_is_off_limits():
    with patch(CALL_MODEL_PATCH_TARGET) as mock_model:
        mock_model.invoke.return_value = AIMessage(content="...")
        call_model(_feedback_state("we want a labeler", setup_stage="purpose"))
    prompt = _sent_system_prompt(mock_model)
    assert "Not established yet" in prompt          # purpose is visibly the open question
    assert "note_for_later" in prompt               # with somewhere to put what they said
    assert "record_purpose" in prompt               # and a way to end the stage


def test_parked_details_are_rendered_for_the_stage_that_uses_them():
    state = _feedback_state("what's next?", setup_stage="content")
    state["design_notes"] = ["flag drive-by product spam", "flag drive-by product spam", "  "]
    state["community_purpose"] = {"community": "bakers", "audience": "members", "goal": "flag spam"}

    with patch(CALL_MODEL_PATCH_TARGET) as mock_model:
        mock_model.invoke.return_value = AIMessage(content="...")
        call_model(state)
    prompt = _sent_system_prompt(mock_model)

    assert prompt.count("flag drive-by product spam") == 1  # deduped, blanks dropped
    assert "Community: bakers" in prompt


def test_missing_setup_stage_key_renders_blank_marker():
    """A state with no 'setup_stage' key at all falls back to '' rather than raising.

    This is now only a defensive fallback: provide_feedback passes setup_stage and
    FeedbackGraphState declares it, so production reaches the prompt with a real stage
    (see test_purpose/content_stage_marker_reaches_the_prompt). A blank marker matches
    none of the prompt's stage branches, so the agent falls through to generic behavior.
    """
    with patch(CALL_MODEL_PATCH_TARGET) as mock_model:
        mock_model.invoke.return_value = AIMessage(content="...")
        call_model(_feedback_state("create a spam label"))
    assert "Setup stage: \n" in _sent_system_prompt(mock_model)


def test_truncated_tool_call_raises_instead_of_staging_empty_args():
    """A response cut off at max_tokens loses the tail of its tool-call JSON and parses
    into a finalize_* call with its rules/labels silently dropped. call_model must raise
    so provide_feedback surfaces it, rather than staging an empty proposal for a vote.

    Regression: rules derivation needs ~1,450 output tokens and the shared model allowed
    1,000, so every finalize_rules call parsed with zero rules (see src/config.py).
    """
    truncated = AIMessage(
        content="",
        tool_calls=[{"name": "finalize_rules", "args": {"rules": []}, "id": "t1"}],
        response_metadata={"stop_reason": "max_tokens"},
        usage_metadata={"input_tokens": 3442, "output_tokens": 1000, "total_tokens": 4442},
    )
    with patch(CALL_MODEL_PATCH_TARGET) as mock_model:
        mock_model.invoke.return_value = truncated
        with pytest.raises(RuntimeError, match="cut off"):
            call_model(_feedback_state("derive rules", setup_stage="rules"))


def test_complete_tool_call_does_not_raise():
    """stop_reason 'tool_use' is the normal completion path and must pass through."""
    complete = AIMessage(
        content="",
        tool_calls=[{"name": "finalize_rules", "args": {"rules": [{"label_identifier": "spam"}]}, "id": "t1"}],
        response_metadata={"stop_reason": "tool_use"},
    )
    with patch(CALL_MODEL_PATCH_TARGET) as mock_model:
        mock_model.invoke.return_value = complete
        out = call_model(_feedback_state("derive rules", setup_stage="rules"))
    assert out["messages"] == [complete]


def test_setup_stage_survives_the_feedback_subgraph_boundary():
    """setup_stage must be declared in FeedbackGraphState or LangGraph silently drops it
    when provide_feedback invokes the subgraph, leaving the prompt's stage branches dead
    while the parent's stage machine keeps advancing. Regression: this went unnoticed
    because every transition is gated on artifacts and votes, never on the agent's
    behavior, so the flow still 'worked' with an unsteered agent.
    """
    assert "setup_stage" in FeedbackGraphState.__annotations__

    from src.agent.feedback.graph import feedback_graph
    with patch(CALL_MODEL_PATCH_TARGET) as mock_model:
        mock_model.invoke.return_value = AIMessage(content="what's this labeler for?")
        feedback_graph.invoke({
            "messages": [HumanMessage(content="we want a labeler")],
            "labeler_config": {},
            "labels": {},
            "setup_stage": "purpose",
        })
    assert "Setup stage: purpose" in _sent_system_prompt(mock_model)


def test_explicit_system_prompt_replaces_the_template():
    """The rules-derivation prompt must arrive intact, with none of FEEDBACK_AGENT_PROMPT's
    per-stage directives mixed in — those tell the agent to design labels, not rules. Only the
    live stage context is appended, so 'what's next?' during rules/preview is still answerable."""
    override = RULES_DERIVATION_PROMPT.format(current_config="", current_rules="", capabilities="", query="")
    with patch(CALL_MODEL_PATCH_TARGET) as mock_model:
        mock_model.invoke.return_value = AIMessage(content="...")
        call_model(_feedback_state("derive rules", system_prompt=override))

    sent = _sent_system_prompt(mock_model)
    assert sent.startswith(override)
    assert "What to do at this stage" not in sent      # the template did not leak in
    assert "Where the group is right now" in sent


def test_the_feedback_agent_is_told_where_the_group_is():
    """Measured: "what do we need to do to move forward?" classifies as feedback 5/5, so this
    agent — not draft_response — is what answers it."""
    state = _feedback_state("what do we need to do to move forward?", setup_stage="content")
    state["stage_context"] = "Setup stage: content\nWaiting on a vote: 1 of 2 approvals so far"

    with patch(CALL_MODEL_PATCH_TARGET) as mock_model:
        mock_model.invoke.return_value = AIMessage(content="...")
        call_model(state)

    assert "1 of 2 approvals so far" in _sent_system_prompt(mock_model)


def test_a_missing_stage_context_is_stated_rather_than_left_blank():
    """A blank section reads as 'nothing pending' to the model; it has to say it doesn't know."""
    with patch(CALL_MODEL_PATCH_TARGET) as mock_model:
        mock_model.invoke.return_value = AIMessage(content="...")
        call_model(_feedback_state("what's next?", setup_stage="purpose"))

    prompt = _sent_system_prompt(mock_model)
    assert "Where the group is right now" in prompt
    assert "Not established yet." in prompt


def test_provide_feedback_passes_the_live_stage_into_the_subgraph():
    state = _state("what's next?", setup_stage="rules")
    state["approvals_needed"] = 2

    with patch(FEEDBACK_GRAPH_PATCH_TARGET) as mock_graph:
        mock_graph.invoke.return_value = {"messages": [AIMessage(content="ok")]}
        provide_feedback(state)

    sent = mock_graph.invoke.call_args[0][0]["stage_context"]
    assert "Setup stage: rules" in sent
    assert "Approvals a card needs in this channel: 2" in sent


# =============================================================================
# provide_feedback: how setup_stage is (or isn't) threaded into feedback_graph
# =============================================================================

FEEDBACK_GRAPH_PATCH_TARGET = "src.agent.brainstorming.nodes.feedback_graph"


def _invoked_feedback_state(setup_stage: str | None) -> dict:
    with patch(FEEDBACK_GRAPH_PATCH_TARGET) as mock_graph:
        mock_graph.invoke.return_value = {"messages": [AIMessage(content="ok")]}
        provide_feedback(_state("let's set this labeler up", setup_stage=setup_stage))
    return mock_graph.invoke.call_args[0][0]


def test_rules_stage_forwards_a_rules_derivation_system_prompt():
    invoked_state = _invoked_feedback_state("rules")
    assert "system_prompt" in invoked_state
    assert "finalize_rules" in invoked_state["system_prompt"]
    # The rules prompt explicitly invites changes before/alongside staging.
    assert "invite changes" in invoked_state["system_prompt"]


def test_preview_lifecycle_forwards_rules_derivation_prompt():
    """The back edge: during the preview lifecycle stage (setup_stage 'complete'), a change
    request is treated as rules-derivation so the group can tweak rules before approving."""
    state = _state("make the cure rule stricter", setup_stage="complete")
    state["lifecycle_stage"] = "preview"
    with patch(FEEDBACK_GRAPH_PATCH_TARGET) as mock_graph:
        mock_graph.invoke.return_value = {"messages": [AIMessage(content="ok")]}
        provide_feedback(state)
    invoked = mock_graph.invoke.call_args[0][0]
    assert "system_prompt" in invoked
    assert "finalize_rules" in invoked["system_prompt"]
    assert invoked["setup_stage"] == "rules"  # the subgraph sees the effective (rules) stage


def test_rules_stage_feeds_existing_rules_into_prompt_for_revision():
    """On a revision turn, the previously staged rules are injected into the prompt so the
    feedback agent edits them instead of re-deriving from scratch."""
    state = _state("loosen the fake_cure rule", setup_stage="rules")
    state["pending_rule_suggestions"] = {
        "msg-1": {
            "proposal": {
                "fake_cure": {
                    "label_identifier": "fake_cure",
                    "include_signals": [{"type": "keyword", "value": "miracle cure"}],
                    "exclude_signals": [],
                    "notes": "catches cure claims",
                }
            },
            "approved_by": [],
        }
    }
    with patch(FEEDBACK_GRAPH_PATCH_TARGET) as mock_graph:
        mock_graph.invoke.return_value = {"messages": [AIMessage(content="ok")]}
        provide_feedback(state)
    system_prompt = mock_graph.invoke.call_args[0][0]["system_prompt"]
    assert "fake_cure" in system_prompt
    assert "miracle cure" in system_prompt
    assert "REVISING" in system_prompt


def test_rules_stage_with_no_prior_rules_says_derive_from_scratch():
    invoked_state = _invoked_feedback_state("rules")
    assert "derive rules from scratch" in invoked_state["system_prompt"].lower()


def test_rules_prompt_allows_config_edits_via_finalize_proposal():
    """The rules stage no longer locks the config: its prompt tells CLEO it may call
    finalize_proposal to change labels/name/description."""
    invoked_state = _invoked_feedback_state("rules")
    assert "finalize_proposal" in invoked_state["system_prompt"]
    assert "not locked" in invoked_state["system_prompt"]


def test_draft_response_constrains_signals_to_real_capabilities():
    """draft_response answers 'question' intents (e.g. 'what do we do next?') and must not
    advertise signal kinds the labeler can't run. The capability guardrail is injected into
    its prompt so it can't invent link/URL analysis, co-occurrence logic, etc."""
    state = _state("what do we do next?", setup_stage="rules")
    state["classification"] = {"intent": "question", "atproto": "labeler", "topic": "labeler"}
    chunk = type("Chunk", (), {"content": "ok"})()
    with patch("src.agent.brainstorming.nodes.llm") as mock_llm:
        mock_llm.stream.return_value = [chunk]
        draft_response(state)
    system_message = mock_llm.stream.call_args[0][0][0]
    assert "out of scope" in system_message.content
    assert "co-occur" in system_message.content        # no "keyword AND link" logic
    assert "account age" in system_message.content      # real capabilities blurb present


def test_config_edit_during_rules_stage_is_staged_as_a_proposal():
    """A finalize_proposal tool call in the rules stage (the group asked to change a label)
    is still extracted and staged for approval — config edits aren't locked out mid-rules."""
    with patch(FEEDBACK_GRAPH_PATCH_TARGET) as mock_graph:
        mock_graph.invoke.return_value = {
            "messages": [_ai_message_with_tool_call(
                "finalize_proposal",
                {"labels": [{"identifier": "spam", "severity": "alert",
                             "blurs": "media", "default_setting": "warn"}]},
            )]
        }
        cmd = provide_feedback(_state("actually add a spam label too", setup_stage="rules"))
    assert cmd.update["pending_proposal"]["labels"][0]["identifier"] == "spam"
    # Stays in the rules stage — a config edit doesn't regress the setup.
    assert cmd.update["setup_stage"] == "rules"


def test_purpose_stage_forwards_setup_stage_to_feedback_graph():
    """provide_feedback forwards setup_stage into every feedback subgraph invocation
    (not just via the 'rules' system_prompt override), so FEEDBACK_AGENT_PROMPT's
    guided setup instructions for 'purpose' reach call_model's default templating."""
    invoked_state = _invoked_feedback_state("purpose")
    assert "system_prompt" not in invoked_state
    assert invoked_state["setup_stage"] == "purpose"


def test_content_stage_forwards_setup_stage_to_feedback_graph():
    """Same as the purpose-stage case above, for the 'content' stage."""
    invoked_state = _invoked_feedback_state("content")
    assert "system_prompt" not in invoked_state
    assert invoked_state["setup_stage"] == "content"


# =============================================================================
# provide_feedback: setup_stage advancement based on config completeness
# =============================================================================


def _ai_message_with_tool_call(name: str, args: dict) -> AIMessage:
    return AIMessage(content="ok", tool_calls=[{"id": "1", "name": name, "args": args}])


PURPOSE_ARGS = {
    "community": "a from-scratch baking community",
    "audience": "our members and newcomers",
    "goal": "highlight posts that fit our ethos and flag product spam",
}


def test_purpose_stays_on_first_engaged_turn():
    """The first purpose-stage turn is CLEO's kickoff question — nothing recorded yet — so the
    stage stays 'purpose'. (Advancement is not gated on a proposal: the purpose stage is
    forbidden to finalize one.)"""
    with patch(FEEDBACK_GRAPH_PATCH_TARGET) as mock_graph:
        mock_graph.invoke.return_value = {
            "messages": [AIMessage(content="What community is this for, and what's the goal?")]
        }
        cmd = provide_feedback(_state("we want a labeler for our community",
                                      setup_stage="purpose", feedback_messages=[]))
    assert cmd.update["setup_stage"] == "purpose"


def test_purpose_stage_holds_while_the_question_is_still_open():
    """A turn having happened is not an answer. The stage ends when the purpose is captured, or
    the group would reach the label stage having never been asked what the labeler is for."""
    prior = [HumanMessage(content="we want a labeler"), AIMessage(content="what's the purpose?")]
    with patch(FEEDBACK_GRAPH_PATCH_TARGET) as mock_graph:
        mock_graph.invoke.return_value = {
            "messages": [AIMessage(content="Noted — and who are the labels for?")]
        }
        cmd = provide_feedback(_state("mark the good stuff and the spam",
                                      setup_stage="purpose", feedback_messages=prior))
    assert cmd.update["setup_stage"] == "purpose"


def test_recorded_purpose_advances_to_content():
    with patch(FEEDBACK_GRAPH_PATCH_TARGET) as mock_graph:
        mock_graph.invoke.return_value = {
            "messages": [_ai_message_with_tool_call("record_purpose", PURPOSE_ARGS)]
        }
        cmd = provide_feedback(_state("we're a from-scratch baking community",
                                      setup_stage="purpose", feedback_messages=[]))
    assert cmd.update["setup_stage"] == "content"
    assert cmd.update["community_purpose"] == PURPOSE_ARGS


def test_purpose_advances_on_the_backstop_so_a_group_cannot_get_wedged():
    """A group that answers sideways forever still moves on — the parked details carry them."""
    prior = []
    for _ in range(PURPOSE_MAX_TURNS - 1):
        prior += [HumanMessage(content="hmm"), AIMessage(content="what's this for?")]
    with patch(FEEDBACK_GRAPH_PATCH_TARGET) as mock_graph:
        mock_graph.invoke.return_value = {"messages": [AIMessage(content="ok — what's the goal?")]}
        cmd = provide_feedback(_state("anyway", setup_stage="purpose", feedback_messages=prior))
    assert cmd.update["setup_stage"] == "content"


def test_an_incomplete_purpose_is_not_recorded():
    """All three parts or nothing — a half-filled purpose would end the stage having learned
    less than it asked for."""
    with patch(FEEDBACK_GRAPH_PATCH_TARGET) as mock_graph:
        mock_graph.invoke.return_value = {
            "messages": [_ai_message_with_tool_call(
                "record_purpose", {"community": "bakers", "audience": "", "goal": "flag spam"}
            )]
        }
        cmd = provide_feedback(_state("we're bakers", setup_stage="purpose", feedback_messages=[]))
    assert "community_purpose" not in cmd.update
    assert cmd.update["setup_stage"] == "purpose"


def test_details_raised_early_are_parked_not_acted_on():
    """The logged failure mode: labels discussed while CLEO is still establishing purpose. They
    are captured for the content stage instead of derailing this one."""
    details = ["mark posts that fit the from-scratch ethos", "flag drive-by product spam"]
    with patch(FEEDBACK_GRAPH_PATCH_TARGET) as mock_graph:
        mock_graph.invoke.return_value = {
            "messages": [_ai_message_with_tool_call("note_for_later", {"details": details})]
        }
        cmd = provide_feedback(_state("two things I'd want marked",
                                      setup_stage="purpose", feedback_messages=[]))
    assert cmd.update["design_notes"] == details
    assert cmd.update["setup_stage"] == "purpose"  # noting is not answering
    assert cmd.update["pending_proposal"] is None  # and it stages nothing


def test_a_turn_spent_only_on_tool_calls_still_says_something():
    """Observed live: the model records the details and ends its turn with no text. The purpose
    stage has no proposal card to fall back on, so without a floor this reaches the group as
    silence on a message they addressed to CLEO."""
    details = ["flag drive-by product spam"]
    silent_turn = AIMessage(
        content=[],  # exactly what the live model returned: tool call made, nothing said
        tool_calls=[{"id": "1", "name": "note_for_later", "args": {"details": details}}],
    )
    with patch(FEEDBACK_GRAPH_PATCH_TARGET) as mock_graph:
        mock_graph.invoke.return_value = {"messages": [silent_turn]}
        cmd = provide_feedback(_state("two things I'd want marked",
                                      setup_stage="purpose", feedback_messages=[]))

    reply = cmd.update["feedback_response"]
    assert PURPOSE_QUESTION in reply       # the question the stage exists to ask
    assert details[0] in reply             # and proof their ask was heard


def test_the_agents_own_words_win_over_the_fallback():
    with patch(FEEDBACK_GRAPH_PATCH_TARGET) as mock_graph:
        mock_graph.invoke.return_value = {"messages": [AIMessage(content="So — who is this for?")]}
        cmd = provide_feedback(_state("hi", setup_stage="purpose", feedback_messages=[]))
    assert cmd.update["feedback_response"] == "So — who is this for?"


def test_parked_details_are_replayed_to_the_next_stage():
    """What the group already said reaches the stage that acts on it, so they aren't asked twice."""
    with patch(FEEDBACK_GRAPH_PATCH_TARGET) as mock_graph:
        mock_graph.invoke.return_value = {"messages": [AIMessage(content="ok")]}
        state = _state("what's next?", setup_stage="content")
        state["design_notes"] = ["flag drive-by product spam"]
        state["community_purpose"] = PURPOSE_ARGS
        provide_feedback(state)
        invoked = mock_graph.invoke.call_args.args[0]
    assert invoked["design_notes"] == ["flag drive-by product spam"]
    assert invoked["community_purpose"] == PURPOSE_ARGS


def test_staged_proposal_pins_default_setting_to_warn():
    """default_setting is not the model's to choose: whatever arrives in the tool call, the
    staging boundary pins it to warn so blurs and severity alone decide behavior."""
    with patch(FEEDBACK_GRAPH_PATCH_TARGET) as mock_graph:
        mock_graph.invoke.return_value = {
            "messages": [_ai_message_with_tool_call(
                "finalize_proposal",
                {"labels": [{"identifier": "harassment", "severity": "alert",
                             "blurs": "content", "default_setting": "hide"}]},
            )]
        }
        cmd = provide_feedback(_state("hide harassment completely", setup_stage="content"))
    labels = cmd.update["pending_proposal"]["labels"]
    assert labels[0]["default_setting"] == "warn"
    # The group's ask to hide is honored through blurs, not default_setting.
    assert labels[0]["blurs"] == "content"


def test_content_stage_does_not_advance_on_draft():
    """Draft-time advancement is restricted to purpose -> content. Staging a label
    proposal while in 'content' must NOT advance to 'rules' — that transition is
    gated on group approval in process_approval_vote."""
    with patch(FEEDBACK_GRAPH_PATCH_TARGET) as mock_graph:
        mock_graph.invoke.return_value = {
            "messages": [_ai_message_with_tool_call(
                "finalize_proposal",
                {"labels": [{"identifier": "spam", "severity": "alert", "blurs": "content", "default_setting": "hide"}]},
            )]
        }
        cmd = provide_feedback(_state("keep out spam", setup_stage="content"))
    assert cmd.update["setup_stage"] == "content"


def test_rules_stage_does_not_advance_on_draft():
    """Staging rules while in 'rules' must NOT advance to 'complete' at draft time —
    that transition is gated on group approval in process_approval_vote."""
    with patch(FEEDBACK_GRAPH_PATCH_TARGET) as mock_graph:
        mock_graph.invoke.return_value = {
            "messages": [_ai_message_with_tool_call(
                "finalize_rules",
                {"rules": [{
                    "label_identifier": "spam",
                    "include_signals": [{"type": "keyword", "value": "buy now"}],
                    "exclude_signals": [],
                    "notes": "catches promo spam",
                }]},
            )]
        }
        cmd = provide_feedback(_state("derive rules for spam", setup_stage="rules"))
    assert cmd.update["setup_stage"] == "rules"


def test_rules_stage_stays_rules_when_no_rules_are_finalized():
    with patch(FEEDBACK_GRAPH_PATCH_TARGET) as mock_graph:
        mock_graph.invoke.return_value = {"messages": [AIMessage(content="what signals should spam catch?")]}
        cmd = provide_feedback(_state("derive rules for spam", setup_stage="rules"))
    assert cmd.update["setup_stage"] == "rules"


def test_complete_stage_stays_complete():
    with patch(FEEDBACK_GRAPH_PATCH_TARGET) as mock_graph:
        mock_graph.invoke.return_value = {"messages": [AIMessage(content="anything else?")]}
        cmd = provide_feedback(_state("make it better", setup_stage="complete"))
    assert cmd.update["setup_stage"] == "complete"


# =============================================================================
# provide_feedback: finalize_rules extraction into pending_classification_rules
# =============================================================================


def test_finalize_rules_call_is_extracted_into_pending_classification_rules():
    with patch(FEEDBACK_GRAPH_PATCH_TARGET) as mock_graph:
        mock_graph.invoke.return_value = {
            "messages": [_ai_message_with_tool_call(
                "finalize_rules",
                {"rules": [{
                    "label_identifier": "spam",
                    "include_groups": [{"all_of": [{"type": "keyword", "value": "buy now"}]}],
                    "exclude_signals": [],
                    "notes": "catches promo spam",
                }]},
            )]
        }
        cmd = provide_feedback(_state("derive rules for spam", setup_stage="rules"))
    rules = cmd.update["pending_classification_rules"]
    assert rules["spam"]["include_groups"] == [{"all_of": [{"type": "keyword", "value": "buy now"}]}]
    assert rules["spam"]["notes"] == "catches promo spam"


def test_no_finalize_rules_call_leaves_pending_classification_rules_none():
    with patch(FEEDBACK_GRAPH_PATCH_TARGET) as mock_graph:
        mock_graph.invoke.return_value = {"messages": [AIMessage(content="what signals should spam catch?")]}
        cmd = provide_feedback(_state("derive rules for spam", setup_stage="rules"))
    assert cmd.update["pending_classification_rules"] is None


def test_unenforceable_signals_are_dropped_before_staging():
    """The staging boundary drops signals the executor can't run and omits any label left
    with no enforceable include signal — nothing unenforceable reaches approval."""
    with patch(FEEDBACK_GRAPH_PATCH_TARGET) as mock_graph:
        mock_graph.invoke.return_value = {
            "messages": [_ai_message_with_tool_call(
                "finalize_rules",
                {"rules": [
                    {  # feasible, but one include group holds an invalid regex -> group dropped
                        "label_identifier": "fake_cure",
                        "include_groups": [
                            {"all_of": [{"type": "keyword", "value": "miracle cure"}]},
                            {"all_of": [{"type": "pattern", "value": "cure(sd?", "plain_name": "a cure word"}]},
                        ],
                        "exclude_signals": [],
                        "notes": "n",
                    },
                    {  # infeasible: only a bad account signal -> whole label omitted
                        "label_identifier": "harassment",
                        "include_groups": [{"all_of": [{"type": "account", "value": "karma > 5"}]}],
                        "exclude_signals": [],
                        "notes": "n",
                    },
                ]},
            )]
        }
        cmd = provide_feedback(_state("derive rules", setup_stage="rules"))
    rules = cmd.update["pending_classification_rules"]
    assert set(rules.keys()) == {"fake_cure"}
    assert rules["fake_cure"]["include_groups"] == [
        {"all_of": [{"type": "keyword", "value": "miracle cure"}]}]


def test_all_unenforceable_rules_stage_nothing():
    with patch(FEEDBACK_GRAPH_PATCH_TARGET) as mock_graph:
        mock_graph.invoke.return_value = {
            "messages": [_ai_message_with_tool_call(
                "finalize_rules",
                {"rules": [{
                    "label_identifier": "harassment",
                    "include_signals": [{"type": "account", "value": "karma > 5"}],
                    "exclude_signals": [],
                    "notes": "n",
                }]},
            )]
        }
        cmd = provide_feedback(_state("derive rules", setup_stage="rules"))
    assert cmd.update["pending_classification_rules"] is None


# =============================================================================
# schema consistency: the tool the model calls vs. the state it reads back
# =============================================================================


def test_signal_input_tool_schema_matches_classification_signal_state_schema():
    """RULES_DERIVATION_PROMPT (src/agent/prompts.py) tells the model which signal types
    are valid, finalize_rules' SignalInput (src/agent/feedback/tools.py) is what actually
    validates the model's tool call, and ClassificationSignal (src/agent/state.py) is what
    the rest of the app reads back out of labeler_config. All three must agree on the set
    of valid signal types, or the model can produce calls the tool schema rejects (or
    silently accepts values the rest of the app doesn't understand)."""
    tool_signal_types = set(get_args(SignalInput.model_fields["type"].annotation))
    state_signal_types = set(get_args(ClassificationSignal.__annotations__["type"]))
    assert tool_signal_types == state_signal_types


# ---------------------------------------------------------------------------
# Context handed to the feedback agent: group deliberation must not be dropped
# ---------------------------------------------------------------------------

def _H(i, text):
    return HumanMessage(content=text, id=f"m{i}")


def _A(i, text):
    return AIMessage(content=text, id=f"m{i}")


# The router stays silent during setup, so a group can send several messages before CLEO is
# triggered. Regression: only the newest reached the agent, so a group's slur and fake-cure
# lists were dropped and CLEO answered about the hashtags in the last message alone.
CHANNEL = [
    _H(1, "purpose"), _A(2, "reply"), _H(3, "just a warning"), _A(4, "staged"),
    _H(5, "the r-slur, spastic, cripple"), _H(6, "MMS, chelation, detox"), _H(7, "hashtags"),
]
_PRIOR_AI = AIMessage(content="staged", id="run-abc")  # model run id, not a Stream id


def test_all_deliberation_since_last_turn_reaches_the_agent():
    got = _unconsumed_human_messages(CHANNEL, [_H(3, "just a warning"), _PRIOR_AI])
    assert [m.content for m in got] == [
        "the r-slur, spastic, cripple", "MMS, chelation, detox", "hashtags"]


def test_trimmed_history_does_not_resurrect_old_messages():
    """prior_feedback is trimmed to FEEDBACK_CONTEXT_WINDOW, so 'not in history' cannot mean
    'new' — the boundary is the last human still in history, not set membership."""
    got = _unconsumed_human_messages(CHANNEL, [_H(5, "the r-slur, spastic, cripple"), _PRIOR_AI])
    assert [m.content for m in got] == ["MMS, chelation, detox", "hashtags"]


def test_first_turn_passes_the_whole_conversation():
    got = _unconsumed_human_messages(CHANNEL, [])
    assert [m.content for m in got] == [
        "purpose", "just a warning", "the r-slur, spastic, cripple", "MMS, chelation, detox", "hashtags"]


def test_boundary_scrolled_out_of_fetch_window_falls_back_to_newest_only():
    """Rather than replaying the whole fetched window as if it were all new."""
    got = _unconsumed_human_messages(CHANNEL, [_H(99, "ancient"), _PRIOR_AI])
    assert [m.content for m in got] == ["hashtags"]


def test_nothing_new_since_last_turn():
    got = _unconsumed_human_messages(CHANNEL, [_H(7, "hashtags"), _PRIOR_AI])
    assert got == []


def test_committed_suggestion_is_not_treated_as_a_pending_proposal():
    """pending_suggestions merges and never deletes, so an approved entry lingers. Without
    a committed check, provide_feedback keeps revising the 'pending' proposal forever."""
    committed = {"proposal": {"display_name": "Approved", "labels": []}, "committed": True}
    state = _state("what next?", setup_stage="rules")
    state["pending_suggestions"] = {"msg-1": committed}
    state["labeler_config"] = {"display_name": "Committed Config", "labels": []}

    with patch(FEEDBACK_GRAPH_PATCH_TARGET) as mock_graph:
        mock_graph.invoke.return_value = {"messages": [AIMessage(content="ok")]}
        provide_feedback(state)
        sent = mock_graph.invoke.call_args[0][0]

    assert sent["labeler_config"]["display_name"] == "Committed Config"


# =============================================================================
# draft_response: whose words reach the channel
# =============================================================================

_PROPOSAL = {
    "display_name": "Home Baking Community Standards",
    "description": "Marks posts that exemplify from-scratch baking values.",
    "labels": [{
        "identifier": "commercial_spam", "severity": "inform", "blurs": "content",
        "locales": [{"lang": "en", "name": "Commercial Spam", "description": "Drive-by product spam."}],
    }],
}


def _feedback_turn_state(intent: str) -> BrainstormingAgentState:
    """A turn that has just come out of provide_feedback with a proposal staged."""
    state = _state("cleo, is that all you need?", setup_stage="content")
    state["classification"] = {"intent": intent, "atproto": "labeler", "topic": "labeler"}
    state["feedback_response"] = "Perfect — I have everything I need for now."
    state["pending_proposal"] = _PROPOSAL
    return state


@pytest.mark.parametrize("intent", ["question", "nudge", "feedback", "generate_code"])
def test_the_feedback_agents_reply_is_surfaced_whatever_the_intent_was(intent):
    """During setup, validate_and_classify sends EVERY intent to provide_feedback. Keying this
    passthrough on intent == 'feedback' instead let the drafting LLM re-narrate a proposal it
    never read — it invented a severity the staged proposal didn't have."""
    with patch("src.agent.brainstorming.nodes.llm") as mock_llm:
        result = draft_response(_feedback_turn_state(intent))

    mock_llm.stream.assert_not_called()
    assert result["draft_response"].startswith("Perfect — I have everything I need for now.")


def test_the_proposal_block_is_appended_to_the_feedback_reply():
    """The block is what the group votes on: the behavior in plain language and the line telling
    them how to approve. It has to travel with the reply, not be paraphrased out of it."""
    with patch("src.agent.brainstorming.nodes.llm") as mock_llm:
        draft = draft_response(_feedback_turn_state("question"))["draft_response"]

    mock_llm.stream.assert_not_called()

    assert "React with 👍🏾 to approve this change." in draft
    assert "Home Baking Community Standards" in draft
    # Rendered from the staged (blurs, severity) pair rather than described from memory.
    assert "neutral note" in draft


def test_a_tool_only_feedback_turn_still_sends_the_block_alone():
    """No prose to surface, but a staged proposal — the card must not be dropped, and must not
    arrive with a leading blank gap where the missing reply was."""
    state = _feedback_turn_state("question")
    state["feedback_response"] = ""

    with patch("src.agent.brainstorming.nodes.llm") as mock_llm:
        draft = draft_response(state)["draft_response"]

    mock_llm.stream.assert_not_called()
    assert draft.startswith("📋 **Proposed update**")


def test_a_turn_that_never_ran_the_feedback_agent_still_drafts():
    """The passthrough must not swallow the normal drafting path — feedback_response is None on
    a turn that went straight to draft_response."""
    state = _state("what do we do next?", setup_stage="rules")
    state["classification"] = {"intent": "question", "atproto": "labeler", "topic": "labeler"}
    chunk = type("Chunk", (), {"content": "here's what's next"})()

    with patch("src.agent.brainstorming.nodes.llm") as mock_llm:
        mock_llm.stream.return_value = [chunk]
        draft = draft_response(state)["draft_response"]

    mock_llm.stream.assert_called_once()
    assert draft == "here's what's next"


def test_each_turn_clears_the_previous_turns_feedback_reply():
    """What makes `feedback_response is not None` mean 'provide_feedback ran on THIS turn'.
    Left uncleared, a reply from an earlier turn would be resent as this turn's answer."""
    with patch(VALIDATE_PATCH_TARGET) as mock_llm:
        mock_llm.invoke.return_value = _llm_result("question", "labeler")
        cmd = validate_and_classify(_state("what is a labeler?", setup_stage="purpose"))

    assert cmd.update["feedback_response"] is None


# =============================================================================
# Stage context: what CLEO knows about where the group is
# =============================================================================

from src.agent.brainstorming.nodes import _stage_context, summarize_conversation


def _pending_anchor_state(approved_by: list[str], needed: int = 2) -> BrainstormingAgentState:
    state = _state("where are we?", setup_stage="content")
    state["approvals_needed"] = needed
    state["pending_suggestions"] = {
        "msg-1": {"proposal": _PROPOSAL, "approved_by": approved_by},
    }
    return state


def test_stage_context_names_the_stage_and_what_ends_it():
    """draft_response used to answer 'what's next?' with no stage in context at all, so it guessed."""
    state = _state("what do we do next?", setup_stage="rules")
    context = _stage_context(state)

    assert "Setup stage: rules" in context
    assert "classification rules" in context
    assert "Nothing is waiting on a vote right now." in context


def test_stage_context_reports_the_live_tally():
    context = _stage_context(_pending_anchor_state(["user-1"], needed=2))

    assert "1 of 2 approvals so far" in context
    assert "👍🏾" in context


def test_stage_context_says_a_single_approval_carries_a_small_channel():
    context = _stage_context(_pending_anchor_state([], needed=1))

    # Phrased by _anchor_what_and_tally, the same helper the group-facing messages use, so the
    # prompt can't describe the vote one way while the reply describes it another.
    assert "no approvals yet — it needs 1" in context
    assert "a single 👍🏾 carries it" in context


def test_lifecycle_stage_takes_over_once_setup_is_complete():
    state = _state("what happens now?", setup_stage="complete")
    state["lifecycle_stage"] = "preview"
    context = _stage_context(state)

    assert "Lifecycle stage: preview" in context
    assert "preview screen" in context
    # The setup stage is still reported, but it isn't what the group is waiting on.
    assert "setup is finished" in context


def test_a_channel_with_no_stage_yet_gets_no_stage_section():
    """The direct /chat endpoint has no setup_stage — better to say nothing than to invent one."""
    assert _stage_context(_state("hello")) == ""


def _draft_system_prompt(state: BrainstormingAgentState) -> str:
    chunk = type("Chunk", (), {"content": "ok"})()
    with patch("src.agent.brainstorming.nodes.llm") as mock_llm:
        mock_llm.stream.return_value = [chunk]
        draft_response(state)
    return mock_llm.stream.call_args[0][0][0].content


def test_the_drafting_prompt_carries_the_live_stage():
    state = _pending_anchor_state(["user-1"], needed=2)
    state["classification"] = {"intent": "question", "atproto": "labeler", "topic": "labeler"}

    prompt = _draft_system_prompt(state)

    assert "Where the group is:" in prompt
    assert "1 of 2 approvals so far" in prompt


def test_the_drafting_prompt_carries_the_behavior_table_and_the_workflow():
    """Both were previously reachable only from the feedback agent, so a question answered by
    draft_response described label behavior — and CLEO's own stages — from memory."""
    state = _state("what does inform mean?", setup_stage="content")
    state["classification"] = {"intent": "question", "atproto": "label", "topic": "label"}

    prompt = _draft_system_prompt(state)

    assert "blurs     severity   what a subscriber sees" in prompt   # LABEL_BEHAVIOR_TABLE
    assert "You are CLEO." in prompt                                 # CLEO_WORKFLOW
    assert "react 👍🏾" in prompt or "reacting to it with 👍🏾" in prompt


def test_the_summary_covers_the_classification_rules():
    """A group that went through the rules stage spent most of its effort there."""
    state = _state("what have we worked on?", setup_stage="complete")
    state["classification_rules"] = {
        "commercial_spam": {
            "label_identifier": "commercial_spam",
            "include_groups": [[{"type": "keyword", "value": "wholesale"}]],
            "notes": "Drive-by product posts",
        }
    }

    with patch("src.agent.brainstorming.nodes.llm") as mock_llm:
        mock_llm.invoke.return_value = type("R", (), {"content": "a summary"})()
        summarize_conversation(state)

    sent = mock_llm.invoke.call_args[0][0][-1].content
    assert "commercial_spam" in sent
    assert "Never reproduce raw patterns" in sent   # the group has never seen the regex


def test_the_summary_skips_the_rules_section_when_there_are_none():
    state = _state("what have we worked on?", setup_stage="content")

    with patch("src.agent.brainstorming.nodes.llm") as mock_llm:
        mock_llm.invoke.return_value = type("R", (), {"content": "a summary"})()
        summarize_conversation(state)

    assert "Current classification rules" not in mock_llm.invoke.call_args[0][0][-1].content


# =============================================================================
# The pending-vote footer: a live vote stated, not left to the model
# =============================================================================

from src.agent.brainstorming.nodes import PENDING_VOTE_FOOTER


def _feedback_reply_with(anchor: dict | None = None, staging: bool = False,
                         rules_anchor: bool = False) -> str:
    state = _state("what about the wording on the second label?", setup_stage="content")
    state["approvals_needed"] = 2
    state["classification"] = {"intent": "feedback", "atproto": "label", "topic": "label"}
    state["feedback_response"] = "Here's what I'd change about the wording."
    if anchor is not None:
        key = "pending_rule_suggestions" if rules_anchor else "pending_suggestions"
        state[key] = {"msg-1": anchor}
    if staging:
        state["pending_proposal"] = _PROPOSAL

    with patch("src.agent.brainstorming.nodes.llm") as mock_llm:
        draft = draft_response(state)["draft_response"]
    mock_llm.stream.assert_not_called()
    return draft


def test_no_footer_when_nothing_is_waiting_on_a_vote():
    """The whole point of the guard: a quiet channel gets no vote chatter."""
    draft = _feedback_reply_with(anchor=None)

    assert "Still waiting on the group" not in draft
    assert draft == "Here's what I'd change about the wording."


def test_the_footer_reports_the_live_tally():
    draft = _feedback_reply_with({"proposal": _PROPOSAL, "approved_by": ["user-1"]})

    assert "Still waiting on the group: the labeler proposal above — 1 of 2 approvals so far." in draft
    # Under the reply, not in place of it.
    assert draft.startswith("Here's what I'd change about the wording.")


def test_the_footer_counts_up_from_zero():
    draft = _feedback_reply_with({"proposal": _PROPOSAL, "approved_by": []})

    assert "no approvals yet — it needs 2" in draft


def test_the_footer_names_a_rules_card_as_rules():
    draft = _feedback_reply_with(
        {"proposal": {"spam": {}}, "approved_by": []}, rules_anchor=True
    )

    assert "set of classification rules above" in draft


def test_no_footer_on_a_turn_that_stages_a_new_card():
    """The anchor still visible from the graph is the one this proposal is about to supersede —
    promotion happens after the run. Its count would be stale the moment the reply lands, and the
    new card carries its own approve line."""
    draft = _feedback_reply_with({"proposal": _PROPOSAL, "approved_by": ["user-1"]}, staging=True)

    assert "Still waiting on the group" not in draft
    assert "React with 👍🏾 to approve this change." in draft   # the new card speaks for itself


def test_a_committed_card_is_not_reported_as_waiting():
    draft = _feedback_reply_with(
        {"proposal": _PROPOSAL, "approved_by": ["user-1", "user-2"], "committed": True}
    )

    assert "Still waiting on the group" not in draft


def test_a_superseded_card_is_not_reported_as_waiting():
    draft = _feedback_reply_with(
        {"proposal": _PROPOSAL, "approved_by": ["user-1"], "superseded": True}
    )

    assert "Still waiting on the group" not in draft


def test_the_footer_and_the_summon_reply_quote_the_same_tally():
    """Both read from _anchor_what_and_tally, so they can't drift into different numbers."""
    from src.agent.brainstorming.nodes import acknowledge_pending

    state = _state("cleo?", setup_stage="content")
    state["approvals_needed"] = 3
    state["pending_suggestions"] = {"msg-1": {"proposal": _PROPOSAL, "approved_by": ["user-1"]}}

    summoned = acknowledge_pending(state)["draft_response"]
    state["feedback_response"] = "Here's what I'd change about the wording."
    with patch("src.agent.brainstorming.nodes.llm"):
        appended = draft_response(state)["draft_response"]

    assert "1 of 3 approvals so far" in summoned
    assert "1 of 3 approvals so far" in appended


def test_the_drafting_path_does_not_get_the_footer():
    """draft_response's own prompt carries the tally and surfaces it in its own words; appending
    it there too would say the same thing twice in one message."""
    state = _state("what is a labeler?", setup_stage="complete")
    state["approvals_needed"] = 2
    state["classification"] = {"intent": "question", "atproto": "labeler", "topic": "labeler"}
    state["pending_suggestions"] = {"msg-1": {"proposal": _PROPOSAL, "approved_by": ["user-1"]}}
    chunk = type("Chunk", (), {"content": "a labeler is..."})()

    with patch("src.agent.brainstorming.nodes.llm") as mock_llm:
        mock_llm.stream.return_value = [chunk]
        draft = draft_response(state)["draft_response"]

    assert "Still waiting on the group" not in draft
    assert "1 of 2 approvals so far" in mock_llm.stream.call_args[0][0][0].content
