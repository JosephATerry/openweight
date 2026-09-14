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
    assert "_apply_schema(config)\n    # record the version only after" in lowered
    assert 'state = "applied" if result.applied else "already current"' in lowered
    assert "database validation successful" in lowered


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


def test_migration_validates_pgvector_schema_role_grants_and_demo_counts() -> None:
    migration = (ROOT / "scripts" / "migrate_database.py").read_text(
        encoding="utf-8"
    )
    persistence = (
        ROOT / "src" / "openweight_platform" / "security" / "persistence.py"
    ).read_text(encoding="utf-8")

    assert "EMBEDDING_DIMENSION" in migration
    assert "vector_dims" in migration
    assert "format_type(attribute.atttypid, attribute.atttypmod)" in migration
    assert "extversion" in migration
    assert "EXPECTED_TABLES" in migration
    assert "EXPECTED_PRIMARY_KEY_TABLES" in migration
    assert "expected constraints are missing" in migration
    assert "approval_sessions_one_pending_per_request" in migration
    assert '"employees": 6' in migration
    assert '"contractors": 4' in migration
    assert '"access_requests": 6' in migration
    assert "rolcanlogin, rolsuper, rolcreaterole, rolcreatedb" in migration
    assert "rolreplication, rolbypassrls" in migration
    assert "pg_auth_members" in migration
    assert "relation.relowner" in migration
    assert "openweight_admin.schema_migrations" in migration
    assert "using hnsw" in migration
    assert "using ivfflat" in migration
    assert "NOSUPERUSER NOCREATEDB NOCREATEROLE" in persistence
    assert "NOREPLICATION NOBYPASSRLS" in persistence
    assert "REVOKE CONNECT, TEMPORARY" in persistence
    assert "REVOKE ALL PRIVILEGES ON SCHEMA openweight_admin" in persistence


def test_migration_failure_output_is_credential_safe() -> None:
    migration = (ROOT / "scripts" / "migrate_database.py").read_text(
        encoding="utf-8"
    )

    assert "except Exception:" in migration
    assert 'print("Database migration or validation failed.", file=sys.stderr)' in migration
    assert "traceback" not in migration.lower()
    assert "config.conninfo" not in migration.split("def main()", maxsplit=1)[1]


def test_pgvector_policy_table_deliberately_uses_exact_scan() -> None:
    vectorstore = (
        ROOT / "src" / "openweight_platform" / "rag" / "vectorstore.py"
    ).read_text(encoding="utf-8").lower()

    assert "vector_size=embedding_dimension" in vectorstore
    assert "hnsw" not in vectorstore
    assert "ivfflat" not in vectorstore
