from langsmith import Client
from openevals.llm import create_llm_as_judge
from langchain_core.messages import HumanMessage
from dotenv import load_dotenv

from src.agent.brainstorming.graph import graph

load_dotenv()


MULTITURN_CONSISTENCY_PROMPT = """
You are evaluating an AI assistant that helps users design Bluesky labelers across multiple conversation turns.

<prior_conversation>
{prior_conversation}
</prior_conversation>

<final_user_message>
{final_question}
</final_user_message>

<ai_final_response>
{answer}
</ai_final_response>

Evaluate whether the AI's final response is consistent with the prior conversation history.

Score True (consistent) if:
- The response acknowledges or builds upon information from prior turns
  (e.g., if a label was discussed in prior turns, the response references it)
- The response maintains logical continuity with the conversation flow
- The response does not contradict information established in prior turns
- For summary requests, the summary accurately reflects the prior discussion

Score False (inconsistent) if:
- The response ignores prior turns completely (treating the conversation as if it just started)
- The response contradicts information established in prior turns
- The response is incoherent given the conversation's progression

If there is no prior conversation (single-turn interaction), always score True
since there is no prior context to be inconsistent with.

Return only True or False.
"""


def target(inputs: dict) -> dict:
    messages = inputs["messages"]
    thread_id = f"mt-eval-{inputs.get('scenario_id', 'unknown')}"

    # Replay all turns except the last to build state in MemorySaver
    for msg in messages[:-1]:
        graph.invoke(
            {"messages": [HumanMessage(content=msg)], "labeler_config": {}},
            config={"configurable": {"thread_id": thread_id}},
        )

    # Final turn is the one we evaluate
    result = graph.invoke(
        {"messages": [HumanMessage(content=messages[-1])]},
        config={"configurable": {"thread_id": thread_id}},
    )

    prior_conversation = "\n".join(f"User: {msg}" for msg in messages[:-1])
    final_question = messages[-1]

    return {
        "answer": result.get("draft_response", ""),
        "prior_conversation": prior_conversation,
        "final_question": final_question,
    }

def multiturn_consistency_evaluator(inputs: dict, outputs: dict, reference_outputs: dict | None = None) -> dict:
    evaluator = create_llm_as_judge(
        prompt=MULTITURN_CONSISTENCY_PROMPT,
        model="openai:o3-mini",
        feedback_key="multiturn_consistency",
    )

    return evaluator(
        prior_conversation=outputs.get("prior_conversation", ""),
        final_question=outputs.get("final_question", ""),
        answer=outputs.get("answer", ""),
    )

def main():
    ls_client = Client()
    experiment_results = ls_client.evaluate(
        target,
        data="Brainstorming multiturn dataset",
        evaluators=[multiturn_consistency_evaluator],
        experiment_prefix="brainstorm-multiturn-eval",
        max_concurrency=2,
    )
    print(experiment_results)


if __name__ == "__main__":
    main()
