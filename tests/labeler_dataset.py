from langsmith import Client
from dotenv import load_dotenv
from labeler_schemas import examples
import argparse

# Loading LangSmith API key and other env vars
load_dotenv()

# Create a dataset
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--update", action="store_true", help="Recreate the dataset if it already exists")
    args = parser.parse_args()

    ls_client = Client()
    DATASET_NAME = "Labeler config schema validation"

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
            dataset_name=DATASET_NAME,
            description="Valid and malformed labeler configs for schema-validation eval",
        )
        ls_client.create_examples(dataset_id=dataset.id, examples=examples)
        print("Created dataset:", dataset.name)

if __name__ == "__main__":
    main()
