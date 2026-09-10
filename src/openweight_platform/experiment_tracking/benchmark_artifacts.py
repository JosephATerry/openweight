"""Validate committed benchmark artifacts and map them to tracking records."""

from __future__ import annotations

import hashlib
import json
import math
import re
import statistics
from collections import defaultdict
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from openweight_platform.benchmarking.datasets import load_policy_cases
from openweight_platform.benchmarking.scoring import VALID_DECISIONS
from openweight_platform.experiment_tracking.models import TrackingRecord
from openweight_platform.orchestration.routing_evaluation import (
    RoutingEvaluationCase,
    ScoredRoutingDecision,
    load_routing_evaluation_cases,
    score_routing_output,
    summarize_routing_scores,
)


MAPPING_VERSION = "benchmark-artifact-v1"
ROUTING_EXPERIMENT = "openweight-platform-routing"
POLICY_EXPERIMENT = "openweight-platform-policy"
_SANITIZED_GENERATION_ERROR = (
    "Backend generation failed before producing a usable response."
)
_SAFE_ERROR_TYPE = re.compile(r"^[A-Za-z_][A-Za-z0-9_.]*$")
_FORBIDDEN_KEYS = {
    "api_key",
    "credentials",
    "environment",
    "environment_variables",
    "hidden_reasoning",
    "reasoning_content",
    "secret",
    "stack_trace",
    "traceback",
}


class BenchmarkArtifactError(ValueError):
    """Raised when a result artifact is invalid or internally inconsistent."""


def map_benchmark_artifact(
    result_path: str | Path,
    repository_root: str | Path,
) -> TrackingRecord:
    """Load, validate, checksum, and normalize a benchmark result artifact."""
    root = Path(repository_root).resolve()
    path = Path(result_path).resolve()
    relative_result = _repository_relative(path, root, "result artifact")

    try:
        result_bytes = path.read_bytes()
        document = json.loads(result_bytes)
    except (OSError, json.JSONDecodeError) as error:
        raise BenchmarkArtifactError(
            "Benchmark result must be a readable JSON document."
        ) from error

    if not isinstance(document, dict):
        raise BenchmarkArtifactError("Benchmark result must be a JSON object.")
    _reject_forbidden_keys(document)

    result_sha256 = hashlib.sha256(result_bytes).hexdigest()
    if _is_routing_document(document):
        return _map_routing(
            document=document,
            result_path=path,
            relative_result=relative_result,
            repository_root=root,
            result_sha256=result_sha256,
        )
    if _is_policy_document(document):
        return _map_policy(
            document=document,
            result_path=path,
            relative_result=relative_result,
            repository_root=root,
            result_sha256=result_sha256,
        )
    raise BenchmarkArtifactError("Unrecognized benchmark result schema.")


def sha256_file(path: str | Path) -> str:
    """Return the SHA-256 checksum of a file without modifying it."""
    digest = hashlib.sha256()
    with Path(path).open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _is_routing_document(document: Mapping[str, Any]) -> bool:
    return {"metadata", "summary", "cases"}.issubset(document)


def _is_policy_document(document: Mapping[str, Any]) -> bool:
    return {"model_name", "dataset", "summary", "cases"}.issubset(document)


def _map_routing(
    *,
    document: Mapping[str, Any],
    result_path: Path,
    relative_result: str,
    repository_root: Path,
    result_sha256: str,
) -> TrackingRecord:
    metadata = _object(document.get("metadata"), "routing metadata")
    summary = _object(document.get("summary"), "routing summary")
    cases = _object_list(document.get("cases"), "routing cases")

    model_identifier = _nonempty_string(
        metadata.get("model_identifier"),
        "routing model_identifier",
    )
    dataset_identifier = _nonempty_string(
        metadata.get("benchmark_path"),
        "routing benchmark_path",
    )
    dataset_path, dataset_relative = _resolve_dataset(
        dataset_identifier,
        repository_root,
    )
    try:
        dataset_cases = load_routing_evaluation_cases(dataset_path)
    except (OSError, ValueError) as error:
        raise BenchmarkArtifactError(
            "Referenced routing dataset is invalid."
        ) from error
    _validate_case_ids(cases, [case.case_id for case in dataset_cases])

    number_of_cases = _integer(
        metadata.get("number_of_cases"),
        "routing number_of_cases",
    )
    if number_of_cases != len(cases):
        raise BenchmarkArtifactError(
            "Routing metadata case count does not match cases."
        )

    generation_config = _object(
        metadata.get("generation_config"),
        "routing generation_config",
    )
    max_new_tokens = _positive_integer(
        generation_config.get("max_new_tokens"),
        "routing max_new_tokens",
    )
    benchmark_version = _nonempty_string(
        metadata.get("benchmark_version"),
        "routing benchmark_version",
    )

    scores: list[ScoredRoutingDecision] = []
    generation_seconds: list[float] = []
    input_tokens: list[int] = []
    output_tokens: list[int] = []
    backend_vram: list[float] = []
    for dataset_case, case_document in zip(
        dataset_cases,
        cases,
        strict=True,
    ):
        scores.append(_validate_routing_case(dataset_case, case_document))
        generation = _object(
            case_document.get("generation"),
            f"routing generation for {dataset_case.case_id}",
        )
        seconds = _nonnegative_number(
            generation.get("generation_seconds"),
            "routing generation_seconds",
        )
        inputs = _nonnegative_integer(
            generation.get("input_tokens"),
            "routing input_tokens",
        )
        outputs = _nonnegative_integer(
            generation.get("output_tokens"),
            "routing output_tokens",
        )
        throughput = _nonnegative_number(
            generation.get("tokens_per_second"),
            "routing tokens_per_second",
        )
        expected_throughput = outputs / seconds if seconds > 0 else 0.0
        _require_close(
            throughput,
            expected_throughput,
            "routing case throughput",
        )
        generation_seconds.append(seconds)
        input_tokens.append(inputs)
        output_tokens.append(outputs)
        backend_vram.append(
            _nonnegative_number(
                generation.get("peak_vram_gib"),
                "routing backend-reported peak_vram_gib",
            )
        )

    recomputed_summary = summarize_routing_scores(scores)
    _validate_summary(summary, recomputed_summary, "routing")
    total_seconds = sum(generation_seconds)
    total_outputs = sum(output_tokens)
    failure_ids = [
        score.case_id
        for score in scores
        if not score.full_decision_correct
    ]

    metrics = {
        **{
            key: float(value)
            for key, value in recomputed_summary.items()
            if key != "total_cases"
        },
        "full_decision_failure_count": float(len(failure_ids)),
        "latency_seconds_mean": statistics.fmean(generation_seconds),
        "latency_seconds_median": statistics.median(generation_seconds),
        "generation_seconds_total": total_seconds,
        "input_tokens_total": float(sum(input_tokens)),
        "output_tokens_total": float(total_outputs),
        "weighted_output_tokens_per_second": (
            total_outputs / total_seconds if total_seconds > 0 else 0.0
        ),
        "backend_reported_peak_vram_gib_max": max(backend_vram),
    }
    tags = _base_tags(
        family="routing",
        model_identifier=model_identifier,
        dataset_path=dataset_relative,
        dataset_sha256=sha256_file(dataset_path),
        result_path=relative_result,
        result_sha256=result_sha256,
        timestamp=document.get("metadata", {}).get("timestamp_utc"),
    )
    _add_failure_ids(tags, failure_ids)

    return TrackingRecord(
        experiment_name=ROUTING_EXPERIMENT,
        run_name=f"{model_identifier} frozen routing result",
        params={
            "benchmark_family": "routing",
            "model_identifier": model_identifier,
            "dataset_path": dataset_relative,
            "case_count": len(cases),
            "max_new_tokens": max_new_tokens,
            "benchmark_version": benchmark_version,
            "mapping_version": MAPPING_VERSION,
        },
        metrics=metrics,
        tags=tags,
        artifact_path=result_path,
    )


def _validate_routing_case(
    dataset_case: RoutingEvaluationCase,
    case_document: Mapping[str, Any],
) -> ScoredRoutingDecision:
    case_id = dataset_case.case_id
    if case_document.get("case_id") != case_id:
        raise BenchmarkArtifactError("Routing result case order is invalid.")
    if case_document.get("question") != dataset_case.question:
        raise BenchmarkArtifactError(
            f"Routing question is inconsistent for {case_id}."
        )
    expected = {"route": dataset_case.expected_route}
    if dataset_case.expected_route == "operations":
        expected["tool_name"] = dataset_case.expected_tool_name
        expected["arguments"] = dataset_case.expected_arguments
    if case_document.get("expected_decision") != expected:
        raise BenchmarkArtifactError(
            f"Routing expected decision is inconsistent for {case_id}."
        )

    raw_output = _string(
        case_document.get("raw_model_output"),
        f"routing raw_model_output for {case_id}",
    )
    score = score_routing_output(dataset_case, raw_output)
    expected_parsed: dict[str, Any] | None = None
    if score.predicted_route is not None:
        expected_parsed = {"route": score.predicted_route}
        if score.predicted_route == "operations":
            expected_parsed["tool_name"] = score.predicted_tool_name
            expected_parsed["arguments"] = score.predicted_arguments

    expected_fields: dict[str, Any] = {
        "parsed_decision": expected_parsed,
        "strict_parse_valid": score.strict_parse_valid,
        "route_correct": score.route_correct,
        "full_decision_correct": score.full_decision_correct,
    }
    if dataset_case.expected_route == "operations":
        expected_fields.update(
            operations_tool_correct=score.operations_tool_correct,
            operations_arguments_correct=score.operations_arguments_correct,
        )
    elif (
        "operations_tool_correct" in case_document
        or "operations_arguments_correct" in case_document
    ):
        raise BenchmarkArtifactError(
            f"Non-operations result contains operations scoring for {case_id}."
        )

    for field, expected_value in expected_fields.items():
        if case_document.get(field) != expected_value:
            raise BenchmarkArtifactError(
                f"Routing score field {field} is inconsistent for {case_id}."
            )
    return score


def _map_policy(
    *,
    document: Mapping[str, Any],
    result_path: Path,
    relative_result: str,
    repository_root: Path,
    result_sha256: str,
) -> TrackingRecord:
    model_identifier = _nonempty_string(
        document.get("model_name"),
        "policy model_name",
    )
    dataset_identifier = _nonempty_string(
        document.get("dataset"),
        "policy dataset",
    )
    dataset_path, dataset_relative = _resolve_dataset(
        dataset_identifier,
        repository_root,
    )
    try:
        dataset_cases = load_policy_cases(dataset_path)
    except (OSError, ValueError) as error:
        raise BenchmarkArtifactError(
            "Referenced policy dataset is invalid."
        ) from error
    summary = _object(document.get("summary"), "policy summary")
    cases = _object_list(document.get("cases"), "policy cases")
    _validate_case_ids(cases, [case.case_id for case in dataset_cases])

    correct_count = 0
    format_valid_count = 0
    strict_format_failures = 0
    generation_failures = 0
    valid_format_incorrect = 0
    generation_seconds: list[float] = []
    throughput_values: list[float] = []
    backend_vram: list[float] = []
    category_results: dict[str, list[bool]] = defaultdict(list)
    failure_ids: list[str] = []

    for dataset_case, case_document in zip(
        dataset_cases,
        cases,
        strict=True,
    ):
        case_id = dataset_case.case_id
        if case_document.get("case_id") != case_id:
            raise BenchmarkArtifactError("Policy result case order is invalid.")
        if case_document.get("category") != dataset_case.category:
            raise BenchmarkArtifactError(
                f"Policy category is inconsistent for {case_id}."
            )
        if case_document.get("expected_decision") != dataset_case.expected_decision:
            raise BenchmarkArtifactError(
                f"Policy expected decision is inconsistent for {case_id}."
            )

        predicted = case_document.get("predicted_decision")
        if predicted is not None and not isinstance(predicted, str):
            raise BenchmarkArtifactError(
                f"Policy predicted decision is invalid for {case_id}."
            )
        format_valid = _boolean(
            case_document.get("format_valid"),
            f"policy format_valid for {case_id}",
        )
        correct = _boolean(
            case_document.get("correct"),
            f"policy correct for {case_id}",
        )
        expected_format_valid = predicted in VALID_DECISIONS
        expected_correct = (
            expected_format_valid
            and predicted == dataset_case.expected_decision
        )
        if format_valid != expected_format_valid or correct != expected_correct:
            raise BenchmarkArtifactError(
                f"Policy scoring is inconsistent for {case_id}."
            )

        error = case_document.get("generation_error")
        if error is not None:
            _validate_generation_error(error, case_id)
            if format_valid or correct:
                raise BenchmarkArtifactError(
                    f"Generation failure is scored inconsistently for {case_id}."
                )
            generation_failures += 1
        elif not format_valid:
            strict_format_failures += 1
        elif not correct:
            valid_format_incorrect += 1

        if correct:
            correct_count += 1
        else:
            failure_ids.append(case_id)
        if format_valid:
            format_valid_count += 1
        category_results[dataset_case.category].append(correct)

        generation_seconds.append(
            _nonnegative_number(
                case_document.get("generation_seconds"),
                f"policy generation_seconds for {case_id}",
            )
        )
        throughput = case_document.get("tokens_per_second")
        if throughput is not None:
            throughput_values.append(
                _nonnegative_number(
                    throughput,
                    f"policy tokens_per_second for {case_id}",
                )
            )
        peak_vram = case_document.get("peak_vram_gib")
        if peak_vram is not None:
            backend_vram.append(
                _nonnegative_number(
                    peak_vram,
                    f"policy backend-reported peak_vram_gib for {case_id}",
                )
            )

    total_cases = len(cases)
    recomputed_summary = {
        "total_cases": total_cases,
        "correct_cases": correct_count,
        "format_valid_cases": format_valid_count,
        "accuracy": correct_count / total_cases if total_cases else 0.0,
        "format_compliance": (
            format_valid_count / total_cases if total_cases else 0.0
        ),
    }
    _validate_summary(summary, recomputed_summary, "policy")

    metrics: dict[str, float] = {
        "accuracy": recomputed_summary["accuracy"],
        "format_compliance": recomputed_summary["format_compliance"],
        "correct_count": float(correct_count),
        "format_valid_count": float(format_valid_count),
        "failure_count": float(len(failure_ids)),
        "strict_format_failure_count": float(strict_format_failures),
        "generation_backend_failure_count": float(generation_failures),
        "valid_format_incorrect_count": float(valid_format_incorrect),
        "latency_seconds_mean": statistics.fmean(generation_seconds),
        "latency_seconds_median": statistics.median(generation_seconds),
        "generation_seconds_total": sum(generation_seconds),
        "measurable_throughput_case_count": float(len(throughput_values)),
    }
    if throughput_values:
        metrics["tokens_per_second_mean"] = statistics.fmean(
            throughput_values
        )
    if backend_vram:
        metrics["backend_reported_peak_vram_gib_max"] = max(backend_vram)

    category_metric_names: set[str] = set()
    for category, values in sorted(category_results.items()):
        metric_name = f"category_accuracy.{_metric_slug(category)}"
        if metric_name in category_metric_names:
            raise BenchmarkArtifactError(
                "Policy category names collide after metric normalization."
            )
        category_metric_names.add(metric_name)
        metrics[metric_name] = sum(values) / len(values)

    tags = _base_tags(
        family="policy",
        model_identifier=model_identifier,
        dataset_path=dataset_relative,
        dataset_sha256=sha256_file(dataset_path),
        result_path=relative_result,
        result_sha256=result_sha256,
        timestamp=document.get("timestamp_utc"),
    )
    _add_failure_ids(tags, failure_ids)
    tags["failure.classification_counts"] = (
        f"strict_format={strict_format_failures},"
        f"generation_backend={generation_failures},"
        f"valid_format_incorrect={valid_format_incorrect}"
    )

    return TrackingRecord(
        experiment_name=POLICY_EXPERIMENT,
        run_name=f"{model_identifier} frozen policy result",
        params={
            "benchmark_family": "policy",
            "model_identifier": model_identifier,
            "dataset_path": dataset_relative,
            "case_count": total_cases,
            "mapping_version": MAPPING_VERSION,
        },
        metrics=metrics,
        tags=tags,
        artifact_path=result_path,
    )


def _validate_generation_error(error: Any, case_id: str) -> None:
    error_document = _object(error, f"generation_error for {case_id}")
    error_type = error_document.get("type")
    message = error_document.get("message")
    if not isinstance(error_type, str) or not _SAFE_ERROR_TYPE.fullmatch(
        error_type
    ):
        raise BenchmarkArtifactError(
            f"Generation error type is unsafe for {case_id}."
        )
    if message != _SANITIZED_GENERATION_ERROR:
        raise BenchmarkArtifactError(
            f"Generation error message is not sanitized for {case_id}."
        )


def _base_tags(
    *,
    family: str,
    model_identifier: str,
    dataset_path: str,
    dataset_sha256: str,
    result_path: str,
    result_sha256: str,
    timestamp: Any,
) -> dict[str, str]:
    tags = {
        "benchmark.family": family,
        "model.identifier": model_identifier,
        "dataset.path": dataset_path,
        "dataset.sha256": dataset_sha256,
        "result.path": result_path,
        "result.sha256": result_sha256,
        "tracking.mapping_version": MAPPING_VERSION,
    }
    if timestamp is not None:
        tags["result.timestamp_utc"] = _nonempty_string(
            timestamp,
            "result timestamp",
        )
    return tags


def _add_failure_ids(tags: dict[str, str], failure_ids: Sequence[str]) -> None:
    if not failure_ids:
        return
    joined = ",".join(failure_ids)
    if len(joined) <= 500:
        tags["failure.case_ids"] = joined


def _validate_case_ids(
    cases: Sequence[Mapping[str, Any]],
    expected_ids: Sequence[str],
) -> None:
    case_ids = [case.get("case_id") for case in cases]
    if any(not isinstance(case_id, str) or not case_id for case_id in case_ids):
        raise BenchmarkArtifactError("Every result case requires a case_id.")
    if len(case_ids) != len(set(case_ids)):
        raise BenchmarkArtifactError("Benchmark result case IDs must be unique.")
    if case_ids != list(expected_ids):
        raise BenchmarkArtifactError(
            "Benchmark result case IDs/order do not match the dataset."
        )


def _validate_summary(
    stored: Mapping[str, Any],
    recomputed: Mapping[str, int | float],
    family: str,
) -> None:
    for key, expected in recomputed.items():
        actual = stored.get(key)
        if isinstance(expected, int):
            if not isinstance(actual, int) or isinstance(actual, bool):
                raise BenchmarkArtifactError(
                    f"{family.title()} summary field {key} is invalid."
                )
            matches = actual == expected
        else:
            matches = _is_number(actual) and math.isclose(
                float(actual),
                float(expected),
                rel_tol=1e-9,
                abs_tol=1e-12,
            )
        if not matches:
            raise BenchmarkArtifactError(
                f"{family.title()} summary field {key} is inconsistent."
            )


def _resolve_dataset(
    identifier: str,
    repository_root: Path,
) -> tuple[Path, str]:
    candidate = Path(identifier)
    path = (
        candidate.resolve()
        if candidate.is_absolute()
        else (repository_root / candidate).resolve()
    )
    relative = _repository_relative(path, repository_root, "dataset")
    if not path.is_file():
        raise BenchmarkArtifactError("Referenced benchmark dataset is missing.")
    return path, relative


def _repository_relative(path: Path, root: Path, label: str) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError as error:
        raise BenchmarkArtifactError(
            f"The {label} must be inside the repository."
        ) from error


def _reject_forbidden_keys(value: Any) -> None:
    if isinstance(value, dict):
        for key, nested in value.items():
            normalized_key = re.sub(r"[^a-z0-9]+", "_", str(key).lower()).strip(
                "_"
            )
            if normalized_key in _FORBIDDEN_KEYS:
                raise BenchmarkArtifactError(
                    "Benchmark result contains a prohibited sensitive field."
                )
            _reject_forbidden_keys(nested)
    elif isinstance(value, list):
        for nested in value:
            _reject_forbidden_keys(nested)


def _metric_slug(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_")
    if not slug:
        raise BenchmarkArtifactError("Policy category cannot form a metric name.")
    return slug


def _object(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, dict):
        raise BenchmarkArtifactError(f"{label} must be an object.")
    return value


def _object_list(value: Any, label: str) -> list[Mapping[str, Any]]:
    if not isinstance(value, list) or not all(
        isinstance(item, dict) for item in value
    ):
        raise BenchmarkArtifactError(f"{label} must be a list of objects.")
    if not value:
        raise BenchmarkArtifactError(f"{label} cannot be empty.")
    return value


def _string(value: Any, label: str) -> str:
    if not isinstance(value, str):
        raise BenchmarkArtifactError(f"{label} must be a string.")
    return value


def _nonempty_string(value: Any, label: str) -> str:
    result = _string(value, label)
    if not result.strip():
        raise BenchmarkArtifactError(f"{label} must be non-empty.")
    return result


def _boolean(value: Any, label: str) -> bool:
    if not isinstance(value, bool):
        raise BenchmarkArtifactError(f"{label} must be a boolean.")
    return value


def _integer(value: Any, label: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise BenchmarkArtifactError(f"{label} must be an integer.")
    return value


def _positive_integer(value: Any, label: str) -> int:
    result = _integer(value, label)
    if result <= 0:
        raise BenchmarkArtifactError(f"{label} must be positive.")
    return result


def _nonnegative_integer(value: Any, label: str) -> int:
    result = _integer(value, label)
    if result < 0:
        raise BenchmarkArtifactError(f"{label} must be nonnegative.")
    return result


def _is_number(value: Any) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool)


def _nonnegative_number(value: Any, label: str) -> float:
    if not _is_number(value) or not math.isfinite(float(value)):
        raise BenchmarkArtifactError(f"{label} must be a finite number.")
    result = float(value)
    if result < 0:
        raise BenchmarkArtifactError(f"{label} must be nonnegative.")
    return result


def _require_close(actual: float, expected: float, label: str) -> None:
    if not math.isclose(actual, expected, rel_tol=1e-9, abs_tol=1e-12):
        raise BenchmarkArtifactError(f"{label} is inconsistent.")


__all__ = [
    "BenchmarkArtifactError",
    "MAPPING_VERSION",
    "POLICY_EXPERIMENT",
    "ROUTING_EXPERIMENT",
    "map_benchmark_artifact",
    "sha256_file",
]
