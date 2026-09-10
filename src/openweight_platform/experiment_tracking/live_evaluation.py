"""Optional post-save tracking for newly executed benchmark evaluations."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass, replace
from pathlib import Path

from openweight_platform.experiment_tracking.benchmark_artifacts import (
    map_benchmark_artifact,
)
from openweight_platform.experiment_tracking.models import (
    ExperimentTracker,
    TrackedRun,
    TrackingRecord,
)


LIVE_RUN_ORIGIN = "live_evaluation"


class LiveTrackingError(RuntimeError):
    """Sanitized failure raised after a benchmark result has been saved."""


@dataclass(frozen=True)
class LiveEvaluationConfig:
    """Small whitelist of configuration known by a live benchmark CLI."""

    backend_name: str
    max_new_tokens: int
    reasoning_strength: str | None = None
    model_alias: str | None = None
    structured_output: bool = False
    structured_output_constraint: str | None = None
    run_name: str | None = None

    def __post_init__(self) -> None:
        if not self.backend_name.strip():
            raise ValueError("backend_name must be non-empty")
        if self.max_new_tokens <= 0:
            raise ValueError("max_new_tokens must be positive")
        if not isinstance(self.structured_output, bool):
            raise TypeError("structured_output must be a boolean")
        for name, value in (
            ("reasoning_strength", self.reasoning_strength),
            ("model_alias", self.model_alias),
            (
                "structured_output_constraint",
                self.structured_output_constraint,
            ),
            ("run_name", self.run_name),
        ):
            if value is not None and not value.strip():
                raise ValueError(f"{name} must be non-empty when provided")
        if self.structured_output:
            if self.structured_output_constraint is None:
                raise ValueError(
                    "structured_output_constraint is required when "
                    "structured_output is enabled"
                )
        elif self.structured_output_constraint is not None:
            raise ValueError(
                "structured_output_constraint requires structured_output"
            )


def build_live_tracking_record(
    *,
    result_path: str | Path,
    repository_root: str | Path,
    config: LiveEvaluationConfig,
    git_commit: str | None = None,
) -> TrackingRecord:
    """Normalize a saved result and add only known live-run metadata."""
    root = Path(repository_root).resolve()
    record = map_benchmark_artifact(result_path, root)
    current_commit = git_commit or resolve_current_git_commit(root)
    family = str(record.params["benchmark_family"])

    params = dict(record.params)
    _add_consistent_param(params, "max_new_tokens", config.max_new_tokens)
    params["backend_name"] = config.backend_name
    params["structured_output"] = config.structured_output
    if config.reasoning_strength is not None:
        params["reasoning_strength"] = config.reasoning_strength
    if config.model_alias is not None:
        params["model_alias"] = config.model_alias
    if config.structured_output_constraint is not None:
        params[
            "structured_output_constraint"
        ] = config.structured_output_constraint

    tags = {
        **record.tags,
        "run_origin": LIVE_RUN_ORIGIN,
        "git.commit": current_commit,
    }
    if "historical_import" in tags or any(
        key.startswith("historical_import.") for key in tags
    ):
        raise LiveTrackingError(
            "Live tracking record contains historical import metadata."
        )

    run_name = config.run_name or (
        f"{config.backend_name} live {family} evaluation"
    )
    return replace(
        record,
        run_name=run_name,
        params=params,
        tags=tags,
    )


def track_live_evaluation(
    *,
    result_path: str | Path,
    repository_root: str | Path,
    config: LiveEvaluationConfig,
    tracker: ExperimentTracker,
) -> TrackedRun:
    """Track a complete saved benchmark result without affecting evaluation."""
    try:
        record = build_live_tracking_record(
            result_path=result_path,
            repository_root=repository_root,
            config=config,
        )
        return tracker.log_record(record)
    except Exception:
        raise LiveTrackingError("Live MLflow tracking failed.") from None


def build_local_experiment_tracker(
    repository_root: str | Path,
) -> ExperimentTracker:
    """Construct the local MLflow adapter only when tracking is requested."""
    from openweight_platform.experiment_tracking.mlflow_tracker import (
        LocalMlflowConfig,
        LocalMlflowTracker,
    )

    return LocalMlflowTracker(
        LocalMlflowConfig.from_repository_root(repository_root)
    )


def resolve_current_git_commit(repository_root: str | Path) -> str:
    """Resolve the current Git commit without logging worktree contents."""
    root = Path(repository_root).resolve()
    try:
        top_level = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "--show-toplevel"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        if Path(top_level).resolve() != root:
            raise LiveTrackingError(
                "Configured repository root is not the Git worktree root."
            )
        commit = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
    except LiveTrackingError:
        raise
    except (OSError, subprocess.CalledProcessError):
        raise LiveTrackingError(
            "Unable to resolve the current Git commit."
        ) from None
    if len(commit) != 40:
        raise LiveTrackingError("Current Git commit is invalid.")
    return commit


def _add_consistent_param(
    params: dict[str, str | int | float | bool],
    key: str,
    value: str | int | float | bool,
) -> None:
    if key in params and params[key] != value:
        raise LiveTrackingError(
            f"Saved result and live configuration disagree on {key}."
        )
    params[key] = value


__all__ = [
    "LIVE_RUN_ORIGIN",
    "LiveEvaluationConfig",
    "LiveTrackingError",
    "build_live_tracking_record",
    "build_local_experiment_tracker",
    "resolve_current_git_commit",
    "track_live_evaluation",
]
