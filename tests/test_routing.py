"""
Tests for validate_and_classify routing.

Mocks the LLM call so tests run without API keys and are fast/deterministic.
"""

from unittest.mock import patch
from langchain_core.messages import HumanMessage

from langgraph.graph import END

from src.agent.brainstorming.nodes import router, validate_and_classify
from src.agent.state import BrainstormingAgentState


def _state(message: str) -> BrainstormingAgentState:
    return {
        "messages": [HumanMessage(content=message)],
        "labeler_config": {},
        "validation": None,
        "classification": None,
        "search_results": None,
        "conversation_summary": None,
        "draft_response": None,
        "reactions": [],
        "pending_proposal": None,
        "pending_suggestions": {},
    }


def _llm_result(intent: str, atproto: str = "bluesky", violation: bool = False, message: str = ""):
    return {"intent": intent, "atproto": atproto, "topic": atproto, "violation": violation, "message": message}


PATCH_TARGET = "src.agent.brainstorming.nodes._validate_and_classify_llm"


def test_question_about_labeler_routes_to_search(monkeypatch):
    with patch(PATCH_TARGET) as mock_llm:
        mock_llm.invoke.return_value = _llm_result("question", "labeler")
        cmd = validate_and_classify(_state("what is a labeler?"))
    assert cmd.goto == "search_documentation"


def test_question_about_bluesky_routes_to_search(monkeypatch):
    with patch(PATCH_TARGET) as mock_llm:
        mock_llm.invoke.return_value = _llm_result("question", "bluesky")
        cmd = validate_and_classify(_state("how does bluesky work?"))
    assert cmd.goto == "search_documentation"


def test_question_about_atproto_routes_to_search(monkeypatch):
    with patch(PATCH_TARGET) as mock_llm:
        mock_llm.invoke.return_value = _llm_result("question", "atproto")
        cmd = validate_and_classify(_state("what is the AT Protocol?"))
    assert cmd.goto == "search_documentation"


def test_feedback_intent_routes_to_provide_feedback(monkeypatch):
    with patch(PATCH_TARGET) as mock_llm:
        mock_llm.invoke.return_value = _llm_result("feedback", "label")
        cmd = validate_and_classify(_state("create a label for spam"))
    assert cmd.goto == "provide_feedback"


def test_summary_intent_routes_to_summarize(monkeypatch):
    with patch(PATCH_TARGET) as mock_llm:
        mock_llm.invoke.return_value = _llm_result("summary", "bluesky")
        cmd = validate_and_classify(_state("can you summarize what we discussed?"))
    assert cmd.goto == "summarize_conversation"

def test_generate_code_intent_routes_to_draft_response_one(monkeypatch):
    with patch(PATCH_TARGET) as mock_llm:
        mock_llm.invoke.return_value = _llm_result("generate_code")
        cmd = validate_and_classify(_state("can you generate code from the current labeler configuration?"))
    assert cmd.goto == "draft_response"

def test_generate_code_intent_routes_to_draft_response_two(monkeypatch):
    with patch(PATCH_TARGET) as mock_llm:
        mock_llm.invoke.return_value = _llm_result("generate_code")
        cmd = validate_and_classify(_state("write code from the current labeler configuration"))
    assert cmd.goto == "draft_response"


def test_show_config_intent_routes_to_draft_response(monkeypatch):
    with patch(PATCH_TARGET) as mock_llm:
        mock_llm.invoke.return_value = _llm_result("show_config", "labeler")
        cmd = validate_and_classify(_state("show me my current labeler config"))
    assert cmd.goto == "draft_response"


def test_violation_routes_to_draft_response(monkeypatch):
    with patch(PATCH_TARGET) as mock_llm:
        mock_llm.invoke.return_value = _llm_result("question", "bluesky", violation=True, message="hate speech")
        cmd = validate_and_classify(_state("some violating message"))
    assert cmd.goto == "draft_response"


def test_violation_overrides_intent(monkeypatch):
    """A violation should route to draft_response even if intent would normally route elsewhere."""
    with patch(PATCH_TARGET) as mock_llm:
        mock_llm.invoke.return_value = _llm_result("feedback", "label", violation=True, message="bad content")
        cmd = validate_and_classify(_state("some violating message"))
    assert cmd.goto == "draft_response"


def test_classify_stores_validation_and_classification(monkeypatch):
    with patch(PATCH_TARGET) as mock_llm:
        mock_llm.invoke.return_value = _llm_result("feedback", "label")
        cmd = validate_and_classify(_state("add a label for gore"))
    assert cmd.update["validation"]["violation"] is False
    assert cmd.update["classification"]["intent"] == "feedback"
    assert cmd.update["classification"]["atproto"] == "label"


def test_unknown_intent_falls_through_to_draft_response(monkeypatch):
    with patch(PATCH_TARGET) as mock_llm:
        mock_llm.invoke.return_value = _llm_result("question", "atproto")
        # atproto topic without bluesky/labeler/label should fall through
        mock_llm.invoke.return_value = {**_llm_result("question", "atproto"), "atproto": "other"}
        cmd = validate_and_classify(_state("something unrelated"))
    assert cmd.goto == "draft_response"


# =============================================================================
# router: force_respond and setup-stage silence
# =============================================================================

ROUTER_LLM_TARGET = "src.agent.brainstorming.nodes.fast_model"


def _router_state(
    message: str, force_respond=None, setup_stage=None, lifecycle_stage=None
) -> BrainstormingAgentState:
    state = _state(message)
    state["force_respond"] = force_respond
    state["setup_stage"] = setup_stage
    state["lifecycle_stage"] = lifecycle_stage
    return state


def test_router_forced_responds_even_during_setup():
    with patch(ROUTER_LLM_TARGET) as mock_llm:
        cmd = router(_router_state("@CLEO what's next?", force_respond=True, setup_stage="purpose"))
    assert cmd.goto == "validate_and_classify"
    mock_llm.invoke.assert_not_called()  # deterministic path, no LLM call


def test_router_stays_silent_during_setup_without_trigger():
    """A plain message during setup (e.g. answering CLEO's question) is not a
    trigger, so CLEO stays silent and waits to be summoned to advance."""
    with patch(ROUTER_LLM_TARGET) as mock_llm:
        cmd = router(_router_state("we're a disability advocacy group in the DMV", setup_stage="purpose"))
    assert cmd.goto == END
    mock_llm.invoke.assert_not_called()  # silenced before the LLM router runs


def test_router_stays_silent_when_setup_complete_without_trigger():
    """'complete' is not the end of the design conversation — lifecycle_stage takes over there.
    Reaching it must not turn the trigger gate off and hand addressing back to the LLM."""
    with patch(ROUTER_LLM_TARGET) as mock_llm:
        cmd = router(_router_state("looks good to me", setup_stage="complete"))
    assert cmd.goto == END
    mock_llm.invoke.assert_not_called()


def test_router_stays_silent_during_lifecycle_without_trigger():
    """Group-to-group chatter while reviewing the preview. A question mark aimed at each other
    ("can't we see that one until it runs?") is not a summons."""
    with patch(ROUTER_LLM_TARGET) as mock_llm:
        cmd = router(
            _router_state(
                "so we can't see that one until it runs against real posts?",
                setup_stage="complete",
                lifecycle_stage="preview",
            )
        )
    assert cmd.goto == END
    mock_llm.invoke.assert_not_called()


def test_router_forced_responds_during_lifecycle():
    """The 🤖/@-mention path still works after setup — silence is trigger-gated, not absolute."""
    with patch(ROUTER_LLM_TARGET) as mock_llm:
        cmd = router(
            _router_state(
                "@CLEO can you show the current rules?",
                force_respond=True,
                setup_stage="complete",
                lifecycle_stage="deploy",
            )
        )
    assert cmd.goto == "validate_and_classify"
    mock_llm.invoke.assert_not_called()


def test_router_uses_llm_when_no_setup_stage():
    """The /chat endpoint has no setup_stage and no group to interrupt, so it keeps the LLM router."""
    with patch(ROUTER_LLM_TARGET) as mock_llm:
        mock_llm.invoke.return_value.content = "NO"
        cmd = router(_router_state("brb, in a meeting", setup_stage=None))
    assert cmd.goto == END
    mock_llm.invoke.assert_called_once()
