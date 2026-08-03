from langsmith import Client
from dotenv import load_dotenv
from queries import (
    question_intent_queries, question_intent_answers,
    feedback_intent_queries, feedback_intent_answers,
    violation_intent_queries, violation_intent_answers,
    show_config_intent_queries, show_config_intent_answers,
    summary_intent_queries, summary_intent_answers,
)
import argparse

# Loading LangSmith API key and other env vars
load_dotenv()

# Create a dataset
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--update", action="store_true", help="Recreate the dataset if it already exists")
    args = parser.parse_args()

    queries = [question_intent_queries, feedback_intent_queries, violation_intent_queries, show_config_intent_queries, summary_intent_queries]
    ans_arrs = [question_intent_answers, feedback_intent_answers, violation_intent_answers, show_config_intent_answers, summary_intent_answers]

    questions = [q for arr in queries for q in arr]
    answers = [a for arr in ans_arrs for a in arr]

    examples = [ {
        "inputs": {"question": questions[i]},
        "outputs": {"answer": answers[i]},
        } 
        for i in range(len(questions))]

    ls_client = Client()
    DATASET_NAME = "Brainstorming general dataset"

    if ls_client.has_dataset(dataset_name=DATASET_NAME):
        if not args.update:
            print(f"Dataset '{DATASET_NAME}' already exists. Use --update to replace its contents.")
            return

        dataset = ls_client.read_dataset(dataset_name=DATASET_NAME)
        existing_ids = [ex.id for ex in ls_client.list_examples(dataset_id=dataset.id)]
        if existing_ids:
            ls_client.delete_examples(existing_ids)
        ls_client.create_examples(dataset_id=dataset.id, examples=examples)
        print(f"Updated '{DATASET_NAME}': replaced {len(existing_ids)} examples")
    else:
        dataset = ls_client.create_dataset(
            dataset_name=DATASET_NAME, description="Dataset for brainstorming agent",
        )
        ls_client.create_examples(dataset_id=dataset.id, examples=examples)
        print("Created dataset:", dataset.name)

if __name__ == "__main__":
    main()
