from langsmith import Client
from dotenv import load_dotenv
from tests.schema_validator import validate_labeler_config

load_dotenv()


def target(inputs: dict) -> dict:
    return {"labeler_config": inputs.get("labeler_config", {})}

def schema_evaluator(inputs: dict, outputs: dict, reference_outputs: dict):
    config = outputs.get("labeler_config", {})
    result = validate_labeler_config(config)

    expected_valid = reference_outputs.get("is_valid", True)
    passed = result["is_valid"] == expected_valid

    parts = [f"valid={result['is_valid']}", f"expected={expected_valid}"]
    if result["errors"]:
        parts.append("errors=" + "; ".join(result["errors"]))
    comment = ", ".join(parts)

    return {
        "key": "schema_validity",
        "score": 1.0 if passed else 0.0,
        "comment": comment,
    }

def main():
    ls_client = Client()
    experiment_results = ls_client.evaluate(
        target,
        data="Labeler config schema validation",
        evaluators=[schema_evaluator],
        experiment_prefix="labeler-schema-eval",
        max_concurrency=1,
    )
    print(experiment_results)

if __name__ == "__main__":
    main()
