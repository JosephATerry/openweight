"""Idempotent import of committed historical benchmark results."""

from __future__ import annotations

import hashlib
import subprocess
from dataclasses import dataclass, replace
from pathlib import Path

from openweight_platform.experiment_tracking.benchmark_artifacts import (
    MAPPING_VERSION,
    map_benchmark_artifact,
)
from openweight_platform.experiment_tracking.models import (
    ExperimentTracker,
    TrackedRun,
)


IMPORT_KEY_TAG = "historical_import.key"


class HistoricalImportError(RuntimeError):
    """Raised when historical import provenance or uniqueness is invalid."""


@dataclass(frozen=True)
class GitProvenance:
    source_artifact_commit: str
    importer_commit: str


@dataclass(frozen=True)
class HistoricalImportResult:
    tracked_run: TrackedRun
    import_key: str
    created: bool


def build_historical_import_key(
    benchmark_family: str,
    result_sha256: str,
) -> str:
    """Build the stable uniqueness key for one mapped frozen artifact."""
    material = (
        f"historical-import:{MAPPING_VERSION}:"
        f"{benchmark_family}:{result_sha256}"
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def import_historical_benchmark(
    *,
    result_path: str | Path,
    repository_root: str | Path,
    tracker: ExperimentTracker,
) -> HistoricalImportResult:
    """Validate and idempotently import one committed result artifact."""
    root = Path(repository_root).resolve()
    path = Path(result_path).resolve()
    record = map_benchmark_artifact(path, root)
    provenance = resolve_git_provenance(path, root)

    family = str(record.params["benchmark_family"])
    result_sha256 = record.tags["result.sha256"]
    import_key = build_historical_import_key(family, result_sha256)
    tags = {
        **record.tags,
        "historical_import": "true",
        IMPORT_KEY_TAG: import_key,
        "git.source_artifact_commit": provenance.source_artifact_commit,
        "git.importer_commit": provenance.importer_commit,
    }
    import_record = replace(record, tags=tags)

    matches = tracker.find_runs_by_tag(
        import_record.experiment_name,
        IMPORT_KEY_TAG,
        import_key,
    )
    if len(matches) > 1:
        raise HistoricalImportError(
            "Multiple MLflow runs share the historical import key."
        )
    if len(matches) == 1:
        return HistoricalImportResult(
            tracked_run=matches[0],
            import_key=import_key,
            created=False,
        )

    tracked_run = tracker.log_record(import_record)
    return HistoricalImportResult(
        tracked_run=tracked_run,
        import_key=import_key,
        created=True,
    )


def resolve_git_provenance(
    result_path: str | Path,
    repository_root: str | Path,
) -> GitProvenance:
    """Return the artifact's last-changing commit and current importer HEAD."""
    root = Path(repository_root).resolve()
    path = Path(result_path).resolve()
    try:
        relative = path.relative_to(root).as_posix()
    except ValueError as error:
        raise HistoricalImportError(
            "Historical result must be inside the repository."
        ) from error

    discovered_root = _git(root, "rev-parse", "--show-toplevel")
    if Path(discovered_root).resolve() != root:
        raise HistoricalImportError(
            "Configured repository root is not the Git worktree root."
        )
    _git(root, "ls-files", "--error-unmatch", "--", relative)
    if _git(root, "status", "--porcelain", "--", relative):
        raise HistoricalImportError(
            "Historical result must match its committed Git version."
        )

    source_commit = _git(
        root,
        "log",
        "-1",
        "--format=%H",
        "--",
        relative,
    )
    if not source_commit:
        raise HistoricalImportError(
            "No Git provenance exists for the historical result."
        )
    importer_commit = _git(root, "rev-parse", "HEAD")
    return GitProvenance(
        source_artifact_commit=source_commit,
        importer_commit=importer_commit,
    )


def _git(root: Path, *arguments: str) -> str:
    try:
        completed = subprocess.run(
            ["git", "-C", str(root), *arguments],
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError):
        raise HistoricalImportError(
            "Unable to establish committed Git provenance."
        ) from None
    return completed.stdout.strip()


__all__ = [
    "GitProvenance",
    "HistoricalImportError",
    "HistoricalImportResult",
    "IMPORT_KEY_TAG",
    "build_historical_import_key",
    "import_historical_benchmark",
    "resolve_git_provenance",
]
