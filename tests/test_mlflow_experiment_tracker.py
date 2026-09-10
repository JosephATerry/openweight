import json
from pathlib import Path

import pytest
from mlflow import MlflowClient
from mlflow.tracking._tracking_service.utils import _get_store

from openweight_platform.experiment_tracking.mlflow_tracker import (
    LocalMlflowConfig,
    LocalMlflowTracker,
    MlflowTrackingError,
)
from openweight_platform.experiment_tracking.models import TrackingRecord


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _record(artifact: Path) -> TrackingRecord:
    return TrackingRecord(
        experiment_name="temporary-routing",
        run_name="historical fake routing",
        params={
            "benchmark_family": "routing",
            "case_count": 2,
            "enabled": True,
        },
        metrics={"accuracy": 0.5, "failure_count": 1.0},
        tags={
            "historical_import.key": "abc123",
            "failure.case_ids": "routing_002",
        },
        source_metadata={"artifact_commit": "deadbeef"},
        artifact_path=artifact,
    )


def test_local_sqlite_tracker_logs_searches_and_reads_artifact(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.delenv("MLFLOW_ALLOW_FILE_STORE", raising=False)
    repository = tmp_path / "repository"
    repository.mkdir()
    artifact = tmp_path / "result.json"
    payload = {"summary": {"accuracy": 0.5}}
    artifact.write_text(json.dumps(payload) + "\n", encoding="utf-8")
    config = LocalMlflowConfig.from_repository_root(repository)
    tracker = LocalMlflowTracker(config)
    project_mlruns_before = (PROJECT_ROOT / "mlruns").exists()

    run = tracker.log_record(_record(artifact))
    matches = tracker.find_runs_by_tag(
        "temporary-routing",
        "historical_import.key",
        "abc123",
    )

    assert run.status == "FINISHED"
    assert matches == [run]
    assert type(_get_store(store_uri=config.tracking_uri)).__name__ == (
        "SqlAlchemyStore"
    )
    client = MlflowClient(tracking_uri=config.tracking_uri)
    stored = client.get_run(run.run_id)
    assert stored.data.params == {
        "benchmark_family": "routing",
        "case_count": "2",
        "enabled": "true",
    }
    assert stored.data.metrics == {"accuracy": 0.5, "failure_count": 1.0}
    assert stored.data.tags["historical_import.key"] == "abc123"
    assert stored.data.tags["failure.case_ids"] == "routing_002"
    assert stored.data.tags["source.artifact_commit"] == "deadbeef"
    downloaded = Path(
        client.download_artifacts(
            run.run_id,
            artifact.name,
            str(tmp_path / "downloaded"),
        )
    )
    assert json.loads(downloaded.read_text(encoding="utf-8")) == payload
    assert "MLFLOW_ALLOW_FILE_STORE" not in __import__("os").environ
    assert config.database_path.is_file()
    assert config.artifact_root.is_dir()
    assert (PROJECT_ROOT / "mlruns").exists() == project_mlruns_before


def test_failed_logging_marks_run_failed_and_sanitizes_exception(
    tmp_path,
    monkeypatch,
) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    artifact = tmp_path / "result.json"
    artifact.write_text("{}\n", encoding="utf-8")
    original_bytes = artifact.read_bytes()
    config = LocalMlflowConfig.from_repository_root(repository)
    tracker = LocalMlflowTracker(config)

    def fail_with_secret(*args, **kwargs):
        del args, kwargs
        raise RuntimeError("API_KEY=secret raw tracking failure")

    monkeypatch.setattr(MlflowClient, "log_artifact", fail_with_secret)

    with pytest.raises(MlflowTrackingError) as captured:
        tracker.log_record(_record(artifact))

    assert str(captured.value) == "MLflow tracking operation failed."
    assert "secret" not in str(captured.value)
    assert artifact.read_bytes() == original_bytes
    client = MlflowClient(tracking_uri=config.tracking_uri)
    experiment = client.get_experiment_by_name("temporary-routing")
    assert experiment is not None
    runs = client.search_runs([experiment.experiment_id])
    assert len(runs) == 1
    assert runs[0].info.status == "FAILED"
