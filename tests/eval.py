import os

# Evals replay fresh histories under a fixed thread_id — use the in-memory checkpointer so
# runs don't accumulate stale state in a checkpoints.sqlite file.
os.environ.setdefault("CHECKPOINT_BACKEND", "memory")

from langsmith import Client
from openevals.llm import create_llm_as_judge
from openevals.prompts import CORRECTNESS_PROMPT
from langchain_core.messages import HumanMessage
from dotenv import load_dotenv

from src.agent.brainstorming.graph import graph

import argparse
import sys

load_dotenv()


def target(inputs: dict) -> dict:
    result = graph.invoke(
        {"messages": [HumanMessage(content=inputs["question"])], "labeler_config": {}},
        config={"configurable": {"thread_id": "eval"}},
    )
    return {"answer": result.get("draft_response", "")}


def correctness_evaluator(inputs: dict, outputs: dict, reference_outputs: dict):
    evaluator = create_llm_as_judge(
        prompt=CORRECTNESS_PROMPT,
        model="openai:o3-mini",
        feedback_key="correctness",
    )
    return evaluator(
        inputs=inputs,
        outputs=outputs,
        reference_outputs=reference_outputs,
    )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--threshold",
        type=float,
        default=None
    )
    args = parser.parse_args()

    ls_client = Client()
    experiment_results = ls_client.evaluate(
        target,
        data="Brainstorming general dataset",
        evaluators=[correctness_evaluator],
        experiment_prefix="brainstorm-eval",
        max_concurrency=2,
    )

    if args.threshold is not None:
        scores = []
        for row in experiment_results:
            for eval_result in row["evaluation_results"]["results"]:
                if eval_result.key == "correctness" and eval_result.score is not None:
                    scores.append(eval_result.score)

        if not scores:
            print("No correctness scores found in evaluation results.")
            sys.exit(1)

        avg_score = sum(scores) / len(scores)
        print(f"Experiment: {experiment_results.experiment_name}")
        print(f"Total examples: {len(scores)}")
        print(f"Average correctness score: {avg_score:.4f}")

        if avg_score < args.threshold:
            print(f"FAILED: Average correctness {avg_score:.4f} is below threshold {args.threshold}.")
            sys.exit(1)
        else:
            print(f"PASSED: Average correctness {avg_score:.4f} meets threshold {args.threshold}.")
    else:
        print(experiment_results)


if __name__ == "__main__":
    main()
