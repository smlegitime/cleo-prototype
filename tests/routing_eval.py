from langsmith import Client
from langchain_core.messages import HumanMessage
from dotenv import load_dotenv
from src.agent.brainstorming.nodes import validate_and_classify
from src.agent.state import BrainstormingAgentState

load_dotenv()


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


def target(inputs: dict) -> dict:
    result = validate_and_classify(_state(inputs["message"]))
    return {
        "intent": result.update["classification"]["intent"],
        "atproto": result.update["classification"]["atproto"],
        "violation": result.update["validation"]["violation"],
        "goto": result.goto,
    }


def routing_evaluator(inputs: dict, outputs: dict, reference_outputs: dict):
    goto_match = outputs.get("goto") == reference_outputs.get("goto")
    violation_match = outputs.get("violation") == reference_outputs.get("violation")

    is_violation = reference_outputs.get("violation", False)

    if is_violation:
        score = goto_match and violation_match
    else:
        intent_match = outputs.get("intent") == reference_outputs.get("intent")
        atproto_match = True
        if reference_outputs.get("intent") == "question":
            atproto_match = outputs.get("atproto") == reference_outputs.get("atproto")
        score = intent_match and goto_match and violation_match and atproto_match

    return {"key": "routing_exact_match", "score": score}


def main():
    ls_client = Client()
    experiment_results = ls_client.evaluate(
        target,
        data="Routing evaluation dataset",
        evaluators=[routing_evaluator],
        experiment_prefix="routing-eval",
        max_concurrency=2,
    )
    print(experiment_results)


if __name__ == "__main__":
    main()
