"""Fixed-dataset evaluation for ranked policy retrieval."""

import json
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from langchain_core.documents import Document

from openweight_platform.rag.retrieval import (
    SemanticStore,
    search_policy_corpus,
)


@dataclass(frozen=True)
class RetrievalEvaluationCase:
    query_id: str
    query: str
    expected_policy_id: str


@dataclass(frozen=True)
class RetrievalEvaluationResult:
    query_id: str
    query: str
    expected_policy_id: str
    ranked_policy_ids: list[str]
    first_relevant_rank: int | None
    hit_at_1: bool
    hit_at_3: bool
    reciprocal_rank: float


def load_retrieval_evaluation_cases(
    path: str | Path,
) -> list[RetrievalEvaluationCase]:
    """Load retrieval evaluation cases from a JSON Lines file."""

    cases = []
    seen_query_ids = set()

    with Path(path).open(encoding="utf-8") as file:
        for line_number, line in enumerate(file, start=1):
            line = line.strip()

            if not line:
                continue

            data = json.loads(line)
            case = RetrievalEvaluationCase(
                query_id=data["query_id"],
                query=data["query"],
                expected_policy_id=data["expected_policy_id"],
            )

            if case.query_id in seen_query_ids:
                raise ValueError(
                    f"Duplicate retrieval query ID on line {line_number}: "
                    f"{case.query_id}"
                )

            if not case.query.strip():
                raise ValueError(
                    f"Retrieval query is empty on line {line_number}"
                )

            seen_query_ids.add(case.query_id)
            cases.append(case)

    return cases


def find_first_relevant_rank(
    ranked_policy_ids: Sequence[str],
    expected_policy_id: str,
) -> int | None:
    """Return the one-based rank of the first chunk from the expected policy."""

    for rank, policy_id in enumerate(ranked_policy_ids, start=1):
        if policy_id == expected_policy_id:
            return rank

    return None


def calculate_reciprocal_rank(
    ranked_policy_ids: Sequence[str],
    expected_policy_id: str,
) -> float:
    """Calculate reciprocal rank for the first expected-policy chunk."""

    rank = find_first_relevant_rank(
        ranked_policy_ids,
        expected_policy_id,
    )
    return 0.0 if rank is None else 1.0 / rank


def evaluate_ranked_documents(
    case: RetrievalEvaluationCase,
    documents: Sequence[Document],
) -> RetrievalEvaluationResult:
    """Evaluate a ranked chunk list for one retrieval case."""

    ranked_policy_ids = [
        str(document.metadata["policy_id"])
        for document in documents
    ]
    first_relevant_rank = find_first_relevant_rank(
        ranked_policy_ids,
        case.expected_policy_id,
    )

    return RetrievalEvaluationResult(
        query_id=case.query_id,
        query=case.query,
        expected_policy_id=case.expected_policy_id,
        ranked_policy_ids=ranked_policy_ids,
        first_relevant_rank=first_relevant_rank,
        hit_at_1=first_relevant_rank == 1,
        hit_at_3=(
            first_relevant_rank is not None
            and first_relevant_rank <= 3
        ),
        reciprocal_rank=(
            0.0
            if first_relevant_rank is None
            else 1.0 / first_relevant_rank
        ),
    )


def evaluate_retrieval_cases(
    cases: Sequence[RetrievalEvaluationCase],
    vector_store: SemanticStore,
    *,
    top_k: int = 3,
) -> list[RetrievalEvaluationResult]:
    """Run cases through the existing semantic retrieval implementation."""

    if top_k <= 0:
        raise ValueError("top_k must be greater than zero")

    results = []

    for case in cases:
        documents = search_policy_corpus(
            vector_store,
            case.query,
            k=top_k,
        )
        results.append(evaluate_ranked_documents(case, documents))

    return results


def summarize_retrieval_results(
    results: Sequence[RetrievalEvaluationResult],
) -> dict[str, int | float]:
    """Aggregate Hit@1, Hit@3, and mean reciprocal rank."""

    total_queries = len(results)

    if total_queries == 0:
        return {
            "total_queries": 0,
            "hit_at_1": 0.0,
            "hit_at_3": 0.0,
            "mrr": 0.0,
        }

    return {
        "total_queries": total_queries,
        "hit_at_1": sum(result.hit_at_1 for result in results) / total_queries,
        "hit_at_3": sum(result.hit_at_3 for result in results) / total_queries,
        "mrr": sum(result.reciprocal_rank for result in results) / total_queries,
    }


def format_retrieval_report(
    results: Sequence[RetrievalEvaluationResult],
) -> str:
    """Format per-query rankings and aggregate metrics for the console."""

    sections = ["Policy retrieval evaluation"]

    for result in results:
        ranked = ", ".join(result.ranked_policy_ids) or "<none>"
        relevant_rank = (
            str(result.first_relevant_rank)
            if result.first_relevant_rank is not None
            else "<none>"
        )
        sections.append(
            "\n".join(
                (
                    f"Query: {result.query_id}",
                    f"  Text: {result.query}",
                    f"  Expected policy: {result.expected_policy_id}",
                    f"  Ranked policies: {ranked}",
                    f"  First relevant rank: {relevant_rank}",
                    f"  Hit@1: {'yes' if result.hit_at_1 else 'no'}",
                    f"  Hit@3: {'yes' if result.hit_at_3 else 'no'}",
                    f"  Reciprocal rank: {result.reciprocal_rank:.4f}",
                )
            )
        )

    summary = summarize_retrieval_results(results)
    sections.append(
        "\n".join(
            (
                "Summary",
                f"  Total queries: {summary['total_queries']}",
                f"  Hit@1: {summary['hit_at_1']:.2%}",
                f"  Hit@3: {summary['hit_at_3']:.2%}",
                f"  MRR: {summary['mrr']:.4f}",
            )
        )
    )
    return "\n\n".join(sections)


def _normalize_dataset_identifier(
    dataset: str | Path,
    repository_root: str | Path | None,
) -> str:
    dataset_path = Path(dataset)

    if repository_root is None:
        return str(dataset_path)

    try:
        relative_path = dataset_path.resolve().relative_to(
            Path(repository_root).resolve()
        )
    except ValueError:
        return str(dataset_path)

    return relative_path.as_posix()


def build_retrieval_result_document(
    *,
    embedding_model_id: str,
    embedding_dimension: int,
    dataset: str | Path,
    top_k: int,
    results: Sequence[RetrievalEvaluationResult],
    timestamp: datetime | None = None,
    repository_root: str | Path | None = None,
) -> dict[str, Any]:
    """Build a JSON-serializable retrieval evaluation result document."""

    if timestamp is None:
        timestamp = datetime.now(timezone.utc)
    elif timestamp.tzinfo is None:
        raise ValueError(
            "Retrieval result timestamp must be timezone-aware."
        )

    timestamp_text = (
        timestamp.astimezone(timezone.utc)
        .isoformat()
        .replace("+00:00", "Z")
    )
    query_results = [
        {
            "query_id": result.query_id,
            "query": result.query,
            "expected_policy_id": result.expected_policy_id,
            "ranked_policy_ids": result.ranked_policy_ids,
            "first_relevant_rank": result.first_relevant_rank,
            "hit_at_1": result.hit_at_1,
            "hit_at_3": result.hit_at_3,
            "reciprocal_rank": result.reciprocal_rank,
        }
        for result in results
    ]

    return {
        "embedding_model_id": embedding_model_id,
        "embedding_dimension": embedding_dimension,
        "dataset": _normalize_dataset_identifier(
            dataset,
            repository_root,
        ),
        "timestamp_utc": timestamp_text,
        "top_k": top_k,
        "results": query_results,
        "summary": summarize_retrieval_results(results),
    }


def save_retrieval_result_document(
    document: dict[str, Any],
    output_path: str | Path | None,
) -> None:
    """Persist a retrieval result document as readable JSON when requested."""

    if output_path is None:
        return

    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(document, indent=2) + "\n",
        encoding="utf-8",
    )
