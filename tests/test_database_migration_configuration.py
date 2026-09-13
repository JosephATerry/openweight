from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_database_migration_is_explicit_versioned_and_additive() -> None:
    source = (ROOT / "scripts" / "migrate_database.py").read_text(
        encoding="utf-8"
    )
    lowered = source.lower()

    assert 'migration_version = 1' in lowered
    assert "create extension if not exists vector" in lowered
    assert "schema_migrations" in lowered
    assert "pg_advisory_lock" in lowered
    assert "postgressaver" in lowered
    assert "--seed-demo-data" in lowered
    assert "drop table" not in lowered
    assert "truncate " not in lowered
    assert "delete from" not in lowered


def test_migration_and_policy_index_are_manual_separate_jobs() -> None:
    jobs = (ROOT / "infra" / "terraform" / "bootstrap_jobs.tf").read_text(
        encoding="utf-8"
    )
    app = (ROOT / "infra" / "terraform" / "container_apps.tf").read_text(
        encoding="utf-8"
    )

    assert 'manual_trigger_config {' in jobs
    assert 'replica_retry_limit          = 0' in jobs
    assert 'scripts/migrate_database.py' in jobs
    assert 'scripts/index_policy_corpus.py' in jobs
    assert 'scripts/migrate_database.py' not in app
    assert 'scripts/index_policy_corpus.py' not in app
