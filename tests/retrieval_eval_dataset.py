from langsmith import Client
from dotenv import load_dotenv
from retrieval_queries import retrieval_eval_questions
import argparse

# Loading LangSmith API key and other env vars
load_dotenv()

# Create a dataset
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--update", action="store_true", help="Recreate the dataset if it already exists")
    args = parser.parse_args()

    examples = [
        {
            "inputs": {"question": q["question"]},
            "outputs": {
                "relevant_sources": q["relevant_sources"],
            },
        }
        for q in retrieval_eval_questions
    ]

    ls_client = Client()
    DATASET_NAME = "Retrieval Eval Dataset"

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
            dataset_name=DATASET_NAME, description="Retrieval ground truth: 15 questions with known-relevant source URLs for retriever subgraph evaluation.",
        )
        ls_client.create_examples(dataset_id=dataset.id, examples=examples)
        print("Created dataset:", dataset.name)

if __name__ == "__main__":
    main()
