"""Orchestration and reporting for policy baseline evaluations."""

import json
from collections.abc import Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from openweight_platform.backends.base import ModelBackend
from openweight_platform.benchmarking.datasets import (
    PolicyEvaluationCase,
    build_benchmark_case,
    load_policy_cases,
)
from openweight_platform.benchmarking.runner import (
    BenchmarkRecord,
    run_benchmark,
)
from openweight_platform.benchmarking.scoring import (
    ScoredPolicyRecord,
    score_policy_record,
    summarize_scores,
)


def match_and_score_records(
    cases: Sequence[PolicyEvaluationCase],
    records: Sequence[BenchmarkRecord],
) -> list[ScoredPolicyRecord]:
    """Match benchmark records to policy cases by ID and score them."""
    cases_by_id: dict[str, PolicyEvaluationCase] = {}

    for case in cases:
        if case.case_id in cases_by_id:
            raise ValueError(f"Duplicate policy case ID: {case.case_id}")

        cases_by_id[case.case_id] = case

    scores = []
    matched_ids = set()

    for record in records:
        if record.case_id in matched_ids:
            raise ValueError(
                f"Duplicate benchmark record ID: {record.case_id}"
            )

        try:
            case = cases_by_id[record.case_id]
        except KeyError as error:
            raise ValueError(
                "Benchmark record has no matching policy case: "
                f"{record.case_id}"
            ) from error

        scores.append(score_policy_record(case, record))
        matched_ids.add(record.case_id)

    missing_ids = cases_by_id.keys() - matched_ids

    if missing_ids:
        missing = ", ".join(sorted(missing_ids))
        raise ValueError(
            f"Policy cases have no benchmark records: {missing}"
        )

    return scores


def run_policy_baseline(
    backend: ModelBackend,
    dataset_path: str | Path,
) -> list[ScoredPolicyRecord]:
    """Load, run, match, and score the policy baseline cases."""
    policy_cases = load_policy_cases(dataset_path)
    benchmark_cases = [
        build_benchmark_case(case)
        for case in policy_cases
    ]
    records = run_benchmark(
        backend=backend,
        cases=benchmark_cases,
    )

    return match_and_score_records(policy_cases, records)


def format_report(scores: Sequence[ScoredPolicyRecord]) -> str:
    """Return a human-readable per-case report and aggregate summary."""
    sections = ["Policy baseline evaluation"]

    for score in scores:
        predicted = score.predicted_decision or "<invalid>"
        case_lines = [
            f"Case: {score.case_id}",
            f"  Category: {score.category}",
            f"  Expected decision: {score.expected_decision}",
            f"  Predicted decision: {predicted}",
            f"  Format valid: {'yes' if score.format_valid else 'no'}",
            f"  Correct: {'yes' if score.correct else 'no'}",
            f"  Generation seconds: {score.generation_seconds:.3f}",
            "  Tokens per second: "
            + _format_optional_metric(score.tokens_per_second, 2),
            "  Peak VRAM: "
            + _format_optional_metric(score.peak_vram_gib, 2, " GiB"),
        ]
        if score.generation_error_type is not None:
            case_lines.append(
                "  Generation error: "
                f"{score.generation_error_type}: "
                f"{score.generation_error_message}"
            )
        sections.append("\n".join(case_lines))

    summary = summarize_scores(list(scores))
    sections.append(
        "\n".join(
            (
                "Summary",
                f"  Total cases: {summary['total_cases']}",
                f"  Correct cases: {summary['correct_cases']}",
                f"  Accuracy: {summary['accuracy']:.2%}",
                "  Format-valid cases: "
                f"{summary['format_valid_cases']}",
                "  Format compliance: "
                f"{summary['format_compliance']:.2%}",
            )
        )
    )

    return "\n\n".join(sections)


def build_result_document(
    model_name: str,
    dataset: str | Path,
    scores: Sequence[ScoredPolicyRecord],
    timestamp: datetime | None = None,
    repository_root: str | Path | None = None,
) -> dict[str, Any]:
    """Build a JSON-serializable policy baseline result document."""
    if timestamp is None:
        timestamp = datetime.now(timezone.utc)
    elif timestamp.tzinfo is None:
        raise ValueError("Baseline result timestamp must be timezone-aware.")

    timestamp_utc = timestamp.astimezone(timezone.utc)
    timestamp_text = timestamp_utc.isoformat().replace("+00:00", "Z")

    case_results = []
    for score in scores:
        case_result: dict[str, Any] = {
            "case_id": score.case_id,
            "category": score.category,
            "expected_decision": score.expected_decision,
            "predicted_decision": score.predicted_decision,
            "format_valid": score.format_valid,
            "correct": score.correct,
            "generation_seconds": score.generation_seconds,
            "tokens_per_second": score.tokens_per_second,
            "peak_vram_gib": score.peak_vram_gib,
        }
        if score.generation_error_type is not None:
            case_result["generation_error"] = {
                "type": score.generation_error_type,
                "message": score.generation_error_message,
            }
        case_results.append(case_result)

    return {
        "model_name": model_name,
        "dataset": normalize_dataset_identifier(
            dataset,
            repository_root,
        ),
        "timestamp_utc": timestamp_text,
        "cases": case_results,
        "summary": summarize_scores(list(scores)),
    }


def _format_optional_metric(
    value: float | None,
    precision: int,
    suffix: str = "",
) -> str:
    if value is None:
        return "unavailable"
    return f"{value:.{precision}f}{suffix}"


def normalize_dataset_identifier(
    dataset: str | Path,
    repository_root: str | Path | None,
) -> str:
    """Use a portable repository-relative dataset identifier when possible."""
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


def save_result_document(
    document: dict[str, Any],
    output_path: str | Path | None,
) -> None:
    """Save a result document as JSON when an output path is provided."""
    if output_path is None:
        return

    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(document, indent=2) + "\n",
        encoding="utf-8",
    )
