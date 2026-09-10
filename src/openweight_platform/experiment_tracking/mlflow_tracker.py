"""Local SQLite MLflow adapter for project-owned experiment tracking."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from mlflow import MlflowClient

from openweight_platform.experiment_tracking.models import (
    TrackedRun,
    TrackingRecord,
    effective_tags,
)


@dataclass(frozen=True)
class LocalMlflowConfig:
    """Paths for repository-local SQLite metadata and file artifacts."""

    repository_root: Path
    runtime_root: Path
    database_path: Path
    artifact_root: Path

    @classmethod
    def from_repository_root(
        cls,
        repository_root: str | Path,
    ) -> LocalMlflowConfig:
        root = Path(repository_root).resolve()
        runtime_root = root / "mlruns"
        return cls(
            repository_root=root,
            runtime_root=runtime_root,
            database_path=runtime_root / "mlflow.db",
            artifact_root=runtime_root / "artifacts",
        )

    @property
    def tracking_uri(self) -> str:
        return f"sqlite:///{self.database_path.as_posix()}"

    @property
    def artifact_uri(self) -> str:
        return self.artifact_root.as_uri()


class MlflowTrackingError(RuntimeError):
    """Sanitized failure from the MLflow adapter."""


class LocalMlflowTracker:
    """Track runs in local SQLite with local filesystem artifacts."""

    def __init__(self, config: LocalMlflowConfig) -> None:
        self._config = config

    @property
    def config(self) -> LocalMlflowConfig:
        return self._config

    def find_runs_by_tag(
        self,
        experiment_name: str,
        tag_key: str,
        tag_value: str,
    ) -> list[TrackedRun]:
        try:
            client = self._client()
            experiment = client.get_experiment_by_name(experiment_name)
            if experiment is None:
                return []
            matches = [
                run
                for run in client.search_runs([experiment.experiment_id])
                if run.data.tags.get(tag_key) == tag_value
            ]
        except Exception:
            raise MlflowTrackingError(
                "MLflow duplicate lookup failed."
            ) from None

        return [
            TrackedRun(
                experiment_name=experiment_name,
                experiment_id=run.info.experiment_id,
                run_id=run.info.run_id,
                status=run.info.status,
            )
            for run in matches
        ]

    def log_record(self, record: TrackingRecord) -> TrackedRun:
        client: MlflowClient | None = None
        run_id: str | None = None
        try:
            client = self._client()
            experiment_id = self._get_or_create_experiment(
                client,
                record.experiment_name,
            )
            tags = {
                "mlflow.runName": record.run_name,
                **effective_tags(record),
            }
            run = client.create_run(experiment_id, tags=tags)
            run_id = run.info.run_id

            for key, value in record.params.items():
                client.log_param(run_id, key, _parameter_text(value))
            for key, value in record.metrics.items():
                client.log_metric(run_id, key, float(value))
            client.log_artifact(run_id, str(record.artifact_path))
            client.set_terminated(run_id, status="FINISHED")
        except Exception:
            if client is not None and run_id is not None:
                try:
                    client.set_terminated(run_id, status="FAILED")
                except Exception:
                    pass
            raise MlflowTrackingError(
                "MLflow tracking operation failed."
            ) from None

        return TrackedRun(
            experiment_name=record.experiment_name,
            experiment_id=experiment_id,
            run_id=run_id,
            status="FINISHED",
        )

    def _client(self) -> MlflowClient:
        self._config.runtime_root.mkdir(parents=True, exist_ok=True)
        self._config.artifact_root.mkdir(parents=True, exist_ok=True)
        return MlflowClient(tracking_uri=self._config.tracking_uri)

    def _get_or_create_experiment(
        self,
        client: MlflowClient,
        experiment_name: str,
    ) -> str:
        experiment = client.get_experiment_by_name(experiment_name)
        if experiment is not None:
            if experiment.artifact_location != self._config.artifact_uri:
                raise MlflowTrackingError(
                    "MLflow experiment artifact location is inconsistent."
                )
            return experiment.experiment_id
        return client.create_experiment(
            experiment_name,
            artifact_location=self._config.artifact_uri,
        )


def _parameter_text(value: str | int | float | bool) -> str:
    if isinstance(value, bool):
        return str(value).lower()
    return str(value)


__all__ = [
    "LocalMlflowConfig",
    "LocalMlflowTracker",
    "MlflowTrackingError",
]
