from dotenv import load_dotenv
from langsmith import Client
from src.agent.retriever.tools import vector_store

load_dotenv()

DATASET_NAME = "Retrieval Eval Dataset"

def retriever_target(inputs: dict) -> dict:
    retriever = vector_store.as_retriever()
    docs = retriever.invoke(inputs["question"])
    sources = sorted({d.metadata.get("source", "") for d in docs if d.metadata.get("source")})
    return {"retrieved_sources": sources}

def retrieval_precision(inputs: dict, outputs: dict, reference_outputs: dict) -> dict:
    """True positives / retrieved count."""
    relevant = set(reference_outputs.get("relevant_sources", []))
    retrieved = set(outputs.get("retrieved_sources", []))
    if not retrieved or not relevant:
        return {"key": "retrieval_precision", "score": 0.0}
    tp = len(retrieved & relevant)
    return {"key": "retrieval_precision", "score": tp / len(retrieved)}

def retrieval_recall(inputs: dict, outputs: dict, reference_outputs: dict) -> dict:
    """True positives / relevant count."""
    relevant = set(reference_outputs.get("relevant_sources", []))
    retrieved = set(outputs.get("retrieved_sources", []))
    if not relevant:
        return {"key": "retrieval_recall", "score": 0.0}
    tp = len(retrieved & relevant)
    return {"key": "retrieval_recall", "score": tp / len(relevant)}

def retrieval_f1(inputs: dict, outputs: dict, reference_outputs: dict) -> dict:
    relevant = set(reference_outputs.get("relevant_sources", []))
    retrieved = set(outputs.get("retrieved_sources", []))
    if not retrieved or not relevant:
        return {"key": "retrieval_f1", "score": 0.0}
    tp = len(retrieved & relevant)
    precision = tp / len(retrieved)
    recall = tp / len(relevant)
    if precision + recall == 0:
        return {"key": "retrieval_f1", "score": 0.0}
    f1 = 2 * precision * recall / (precision + recall)
    return {"key": "retrieval_f1", "score": f1}

def main():
    ls_client = Client()
    experiment_results = ls_client.evaluate(
        retriever_target,
        data=DATASET_NAME,
        evaluators=[retrieval_precision, retrieval_recall, retrieval_f1],
        experiment_prefix="retriever-eval",
        max_concurrency=2,
    )
    print(experiment_results)

if __name__ == "__main__":
    main()
