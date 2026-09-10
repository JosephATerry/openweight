"""Backend-neutral experiment tracking contracts and test doubles."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol, TypeAlias


ParameterValue: TypeAlias = str | int | float | bool


@dataclass(frozen=True)
class TrackingRecord:
    """One normalized run ready for a tracking backend."""

    experiment_name: str
    run_name: str
    params: Mapping[str, ParameterValue]
    metrics: Mapping[str, float]
    tags: Mapping[str, str]
    artifact_path: Path
    source_metadata: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.experiment_name.strip():
            raise ValueError("experiment_name must be non-empty")
        if not self.run_name.strip():
            raise ValueError("run_name must be non-empty")
        if not self.artifact_path.is_file():
            raise ValueError("artifact_path must identify an existing file")


@dataclass(frozen=True)
class TrackedRun:
    """Small backend-neutral reference to a tracked run."""

    experiment_name: str
    experiment_id: str
    run_id: str
    status: str


class ExperimentTracker(Protocol):
    """Minimal interface needed by historical and future run logging."""

    def find_runs_by_tag(
        self,
        experiment_name: str,
        tag_key: str,
        tag_value: str,
    ) -> list[TrackedRun]:
        """Return runs whose tag exactly matches the supplied value."""

    def log_record(self, record: TrackingRecord) -> TrackedRun:
        """Persist one normalized tracking record."""


class NoOpExperimentTracker:
    """Tracking implementation that deliberately performs no writes."""

    def find_runs_by_tag(
        self,
        experiment_name: str,
        tag_key: str,
        tag_value: str,
    ) -> list[TrackedRun]:
        del experiment_name, tag_key, tag_value
        return []

    def log_record(self, record: TrackingRecord) -> TrackedRun:
        return TrackedRun(
            experiment_name=record.experiment_name,
            experiment_id="noop",
            run_id="noop",
            status="SKIPPED",
        )


class InMemoryExperimentTracker:
    """Simple recording fake for tests and tracking-disabled workflows."""

    def __init__(self) -> None:
        self.records: list[TrackingRecord] = []
        self.runs: list[TrackedRun] = []

    def find_runs_by_tag(
        self,
        experiment_name: str,
        tag_key: str,
        tag_value: str,
    ) -> list[TrackedRun]:
        return [
            run
            for record, run in zip(self.records, self.runs, strict=True)
            if record.experiment_name == experiment_name
            and _effective_tags(record).get(tag_key) == tag_value
        ]

    def log_record(self, record: TrackingRecord) -> TrackedRun:
        run_number = len(self.runs) + 1
        run = TrackedRun(
            experiment_name=record.experiment_name,
            experiment_id=f"memory-experiment-{record.experiment_name}",
            run_id=f"memory-run-{run_number}",
            status="FINISHED",
        )
        self.records.append(record)
        self.runs.append(run)
        return run


def effective_tags(record: TrackingRecord) -> dict[str, str]:
    """Return explicit tags plus namespaced optional source metadata."""
    return _effective_tags(record)


def _effective_tags(record: TrackingRecord) -> dict[str, str]:
    source_tags = {
        f"source.{key}": value
        for key, value in record.source_metadata.items()
    }
    collisions = source_tags.keys() & record.tags.keys()
    if collisions:
        names = ", ".join(sorted(collisions))
        raise ValueError(f"Tracking tag collision: {names}")
    return {**source_tags, **record.tags}


__all__ = [
    "ExperimentTracker",
    "InMemoryExperimentTracker",
    "NoOpExperimentTracker",
    "ParameterValue",
    "TrackedRun",
    "TrackingRecord",
    "effective_tags",
]
