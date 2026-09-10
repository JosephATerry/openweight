from pathlib import Path

from openweight_platform.experiment_tracking.mlflow_tracker import (
    LocalMlflowConfig,
)
from openweight_platform.experiment_tracking.models import (
    InMemoryExperimentTracker,
    NoOpExperimentTracker,
    TrackingRecord,
)


def _record(artifact_path: Path) -> TrackingRecord:
    return TrackingRecord(
        experiment_name="test-experiment",
        run_name="test-run",
        params={"model": "fake"},
        metrics={"accuracy": 1.0},
        tags={"kind": "test"},
        artifact_path=artifact_path,
    )


def test_noop_tracker_performs_no_writes(tmp_path) -> None:
    artifact = tmp_path / "result.json"
    artifact.write_text("{}\n", encoding="utf-8")
    before = set(tmp_path.iterdir())

    tracker = NoOpExperimentTracker()
    run = tracker.log_record(_record(artifact))

    assert tracker.find_runs_by_tag("test-experiment", "kind", "test") == []
    assert run.run_id == "noop"
    assert run.status == "SKIPPED"
    assert set(tmp_path.iterdir()) == before


def test_in_memory_tracker_records_without_mlflow_state(tmp_path) -> None:
    artifact = tmp_path / "result.json"
    artifact.write_text("{}\n", encoding="utf-8")
    tracker = InMemoryExperimentTracker()
    record = _record(artifact)

    run = tracker.log_record(record)

    assert tracker.records == [record]
    assert tracker.find_runs_by_tag(
        "test-experiment",
        "kind",
        "test",
    ) == [run]
    assert not (tmp_path / "mlruns").exists()


def test_local_config_is_lazy_and_uses_sqlite_and_file_uris(tmp_path) -> None:
    repository_root = tmp_path / "repository"
    repository_root.mkdir()

    config = LocalMlflowConfig.from_repository_root(repository_root)

    assert config.runtime_root == repository_root / "mlruns"
    assert config.database_path == repository_root / "mlruns/mlflow.db"
    assert config.artifact_root == repository_root / "mlruns/artifacts"
    assert config.tracking_uri == (
        f"sqlite:///{repository_root.as_posix()}/mlruns/mlflow.db"
    )
    assert config.artifact_uri == (
        f"file://{repository_root.as_posix()}/mlruns/artifacts"
    )
    assert not config.runtime_root.exists()
