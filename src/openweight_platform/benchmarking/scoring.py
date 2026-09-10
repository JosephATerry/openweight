from dataclasses import dataclass

from openweight_platform.benchmarking.datasets import PolicyEvaluationCase
from openweight_platform.benchmarking.runner import BenchmarkRecord


VALID_DECISIONS = {
    "APPROVE",
    "DENY",
    "NEEDS_INFO",
}


@dataclass(frozen=True)
class ScoredPolicyRecord:
    case_id: str
    category: str
    expected_decision: str
    predicted_decision: str | None
    format_valid: bool
    correct: bool
    response: str
    generation_seconds: float
    tokens_per_second: float | None
    peak_vram_gib: float | None
    generation_error_type: str | None = None
    generation_error_message: str | None = None


def parse_decision(response: str) -> str | None:
    normalized = response.strip().upper()

    if normalized in VALID_DECISIONS:
        return normalized

    return None


def score_policy_record(
    case: PolicyEvaluationCase,
    record: BenchmarkRecord,
) -> ScoredPolicyRecord:
    if case.case_id != record.case_id:
        raise ValueError(
            "Evaluation case and benchmark record have different case IDs."
        )

    predicted_decision = parse_decision(record.response)

    format_valid = predicted_decision is not None

    correct = (
        format_valid
        and predicted_decision == case.expected_decision
    )

    return ScoredPolicyRecord(
        case_id=case.case_id,
        category=case.category,
        expected_decision=case.expected_decision,
        predicted_decision=predicted_decision,
        format_valid=format_valid,
        correct=correct,
        response=record.response,
        generation_seconds=record.generation_seconds,
        tokens_per_second=record.tokens_per_second,
        peak_vram_gib=record.peak_vram_gib,
        generation_error_type=record.generation_error_type,
        generation_error_message=record.generation_error_message,
    )


def summarize_scores(
    scores: list[ScoredPolicyRecord],
) -> dict[str, float | int]:
    total = len(scores)

    if total == 0:
        return {
            "total_cases": 0,
            "correct_cases": 0,
            "format_valid_cases": 0,
            "accuracy": 0.0,
            "format_compliance": 0.0,
        }

    correct_cases = sum(score.correct for score in scores)
    format_valid_cases = sum(score.format_valid for score in scores)

    return {
        "total_cases": total,
        "correct_cases": correct_cases,
        "format_valid_cases": format_valid_cases,
        "accuracy": correct_cases / total,
        "format_compliance": format_valid_cases / total,
    }
