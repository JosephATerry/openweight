#!/usr/bin/env python3
"""Apply explicit, additive OpenWeight PostgreSQL migrations."""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from dataclasses import dataclass

import psycopg
from langgraph.checkpoint.postgres import PostgresSaver

from openweight_platform.api.config import DatabaseConfig
from openweight_platform.operations.database import (
    setup_operations_database,
    setup_operations_schema,
)
from openweight_platform.rag.database import PostgresConfig
from openweight_platform.rag.embeddings import EMBEDDING_DIMENSION
from openweight_platform.rag.vectorstore import initialize_policy_vector_table
from openweight_platform.security.persistence import (
    ensure_runtime_database_role,
    grant_runtime_database_role,
    setup_security_database,
)


MIGRATION_VERSION = 1
MIGRATION_NAME = "azure_initial_schema"
LOCK_NAME = "openweight-database-migrations"
EXPECTED_SCHEMAS = ("openweight_admin", "operations", "security")
EXPECTED_TABLES = (
    "openweight_admin.schema_migrations",
    "operations.access_requests",
    "operations.contractors",
    "operations.employees",
    "public.checkpoint_blobs",
    "public.checkpoint_migrations",
    "public.checkpoint_writes",
    "public.checkpoints",
    "public.policy_chunks",
    "security.approval_sessions",
    "security.execution_ledger",
)
EXPECTED_PRIMARY_KEY_TABLES = EXPECTED_TABLES
EXPECTED_DEMO_COUNTS = {
    "employees": 6,
    "contractors": 4,
    "access_requests": 6,
}


@dataclass(frozen=True)
class MigrationResult:
    applied: bool
    vector_version: str


def _database_config(source: PostgresConfig) -> DatabaseConfig:
    return DatabaseConfig(
        database=source.database,
        user=source.user,
        password=source.password,
        host=source.host,
        port=source.port,
        sslmode=source.sslmode,
        sslrootcert=source.sslrootcert,
    )


def _ensure_migration_ledger(config: DatabaseConfig) -> None:
    with psycopg.connect(**config.connect_kwargs) as connection:
        with connection.cursor() as cursor:
            cursor.execute("CREATE SCHEMA IF NOT EXISTS openweight_admin")
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS openweight_admin.schema_migrations (
                    version INTEGER PRIMARY KEY,
                    name TEXT NOT NULL UNIQUE,
                    applied_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
            )


def _is_applied(config: DatabaseConfig) -> bool:
    with psycopg.connect(**config.connect_kwargs) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT EXISTS (
                    SELECT 1
                    FROM openweight_admin.schema_migrations
                    WHERE version = %s AND name = %s
                )
                """,
                (MIGRATION_VERSION, MIGRATION_NAME),
            )
            return cursor.fetchone() == (True,)


def _record_migration(config: DatabaseConfig) -> None:
    with psycopg.connect(**config.connect_kwargs) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO openweight_admin.schema_migrations (version, name)
                VALUES (%s, %s)
                """,
                (MIGRATION_VERSION, MIGRATION_NAME),
            )


def _apply_schema(config: DatabaseConfig) -> None:
    with psycopg.connect(**config.connect_kwargs) as connection:
        with connection.cursor() as cursor:
            cursor.execute("CREATE EXTENSION IF NOT EXISTS vector")
    setup_operations_schema(config)  # type: ignore[arg-type]
    setup_security_database(config)
    with PostgresSaver.from_conn_string(config.conninfo) as saver:
        saver.setup()
    engine = initialize_policy_vector_table(config)  # type: ignore[arg-type]
    asyncio.run(engine.close())


def _apply_current_migration(
    config: DatabaseConfig,
    runtime_role: str,
    runtime_password: str,
) -> bool:
    if _is_applied(config):
        return False
    # Role creation/password rotation belongs to migration version 1. An
    # already-current validation run must not silently rotate credentials.
    ensure_runtime_database_role(config, runtime_role, runtime_password)
    _apply_schema(config)
    # Record the version only after every schema operation has succeeded.
    _record_migration(config)
    return True


def _validate_postconditions(
    config: DatabaseConfig,
    runtime_role: str,
    *,
    expect_demo_data: bool,
) -> str:
    """Validate schema, pgvector behavior, and the runtime authorization boundary."""

    with psycopg.connect(**config.connect_kwargs) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT extversion FROM pg_extension WHERE extname = 'vector'"
            )
            vector_row = cursor.fetchone()
            if vector_row is None or not vector_row[0]:
                raise RuntimeError("Database validation failed: vector is unavailable")
            vector_version = str(vector_row[0])

            cursor.execute(
                "SELECT vector_dims("
                f"array_fill(0::real, ARRAY[{EMBEDDING_DIMENSION}])::vector)"
            )
            if cursor.fetchone() != (EMBEDDING_DIMENSION,):
                raise RuntimeError(
                    "Database validation failed: vector dimension is unexpected"
                )

            cursor.execute(
                """
                SELECT format_type(attribute.atttypid, attribute.atttypmod)
                FROM pg_attribute attribute
                WHERE attribute.attrelid = 'public.policy_chunks'::regclass
                  AND attribute.attname = 'embedding'
                  AND NOT attribute.attisdropped
                """
            )
            if cursor.fetchone() != (f"vector({EMBEDDING_DIMENSION})",):
                raise RuntimeError(
                    "Database validation failed: policy vector dimension is unexpected"
                )

            cursor.execute(
                """
                SELECT count(*)
                FROM openweight_admin.schema_migrations
                WHERE version = %s AND name = %s
                """,
                (MIGRATION_VERSION, MIGRATION_NAME),
            )
            if cursor.fetchone() != (1,):
                raise RuntimeError(
                    "Database validation failed: migration version is not unique"
                )

            cursor.execute(
                "SELECT nspname FROM pg_namespace WHERE nspname = ANY(%s)",
                (list(EXPECTED_SCHEMAS),),
            )
            actual_schemas = {str(row[0]) for row in cursor.fetchall()}
            if actual_schemas != set(EXPECTED_SCHEMAS):
                raise RuntimeError("Database validation failed: schema set is incomplete")

            cursor.execute(
                """
                SELECT expected_name, to_regclass(expected_name)::text
                FROM unnest(%s::text[]) AS expected_name
                """,
                (list(EXPECTED_TABLES),),
            )
            missing_tables = [row[0] for row in cursor.fetchall() if row[1] is None]
            if missing_tables:
                raise RuntimeError("Database validation failed: table set is incomplete")

            cursor.execute(
                """
                SELECT expected_name,
                       EXISTS (
                           SELECT 1
                           FROM pg_constraint
                           WHERE conrelid = to_regclass(expected_name)
                             AND contype = 'p'
                       )
                FROM unnest(%s::text[]) AS expected_name
                """,
                (list(EXPECTED_PRIMARY_KEY_TABLES),),
            )
            if any(not bool(row[1]) for row in cursor.fetchall()):
                raise RuntimeError(
                    "Database validation failed: primary-key constraint is missing"
                )

            cursor.execute(
                """
                SELECT
                    (SELECT count(*) FROM pg_constraint
                     WHERE conrelid = 'openweight_admin.schema_migrations'::regclass
                       AND contype = 'u'),
                    (SELECT count(*) FROM pg_constraint
                     WHERE conrelid = 'security.approval_sessions'::regclass
                       AND contype = 'c'),
                    (SELECT count(*) FROM pg_constraint
                     WHERE conrelid = 'security.execution_ledger'::regclass
                       AND contype = 'c')
                """
            )
            if cursor.fetchone() != (1, 2, 2):
                raise RuntimeError(
                    "Database validation failed: expected constraints are missing"
                )

            cursor.execute(
                """
                SELECT EXISTS (
                    SELECT 1 FROM pg_indexes
                    WHERE schemaname = 'security'
                      AND indexname = 'approval_sessions_one_pending_per_request'
                      AND indexdef ILIKE '%WHERE%state%pending%'
                )
                """
            )
            if cursor.fetchone() != (True,):
                raise RuntimeError(
                    "Database validation failed: approval uniqueness index is missing"
                )

            cursor.execute(
                """
                SELECT indexdef
                FROM pg_indexes
                WHERE schemaname = 'public' AND tablename = 'policy_chunks'
                """
            )
            policy_indexes = "\n".join(str(row[0]).lower() for row in cursor.fetchall())
            if "using hnsw" in policy_indexes or "using ivfflat" in policy_indexes:
                raise RuntimeError(
                    "Database validation failed: an unreviewed ANN index exists"
                )

            cursor.execute(
                """
                SELECT rolcanlogin, rolsuper, rolcreaterole, rolcreatedb,
                       rolreplication, rolbypassrls
                FROM pg_roles
                WHERE rolname = %s
                """,
                (runtime_role,),
            )
            if cursor.fetchone() != (True, False, False, False, False, False):
                raise RuntimeError(
                    "Database validation failed: application role attributes are unsafe"
                )

            cursor.execute(
                """
                SELECT EXISTS (
                    SELECT 1
                    FROM pg_auth_members membership
                    JOIN pg_roles member ON member.oid = membership.member
                    WHERE member.rolname = %s
                )
                """,
                (runtime_role,),
            )
            if cursor.fetchone() != (False,):
                raise RuntimeError(
                    "Database validation failed: application role inherits another role"
                )

            cursor.execute(
                """
                SELECT
                    EXISTS (
                        SELECT 1
                        FROM pg_namespace namespace
                        JOIN pg_roles role ON role.oid = namespace.nspowner
                        WHERE role.rolname = %s
                    ),
                    EXISTS (
                        SELECT 1
                        FROM pg_class relation
                        JOIN pg_roles role ON role.oid = relation.relowner
                        WHERE role.rolname = %s
                          AND relation.relkind IN ('r', 'p', 'S', 'v', 'm')
                    )
                """,
                (runtime_role, runtime_role),
            )
            if cursor.fetchone() != (False, False):
                raise RuntimeError(
                    "Database validation failed: application role owns objects"
                )

            cursor.execute(
                """
                SELECT
                    has_database_privilege(%s, current_database(), 'CONNECT'),
                    has_database_privilege(%s, current_database(), 'TEMPORARY'),
                    has_schema_privilege(%s, 'openweight_admin', 'USAGE'),
                    has_table_privilege(
                        %s, 'openweight_admin.schema_migrations', 'SELECT'
                    )
                """,
                (runtime_role, runtime_role, runtime_role, runtime_role),
            )
            if cursor.fetchone() != (True, False, False, False):
                raise RuntimeError(
                    "Database validation failed: application database boundary is unsafe"
                )

            for schema_name in ("operations", "security", "public"):
                cursor.execute(
                    "SELECT has_schema_privilege(%s, %s, 'USAGE'), "
                    "has_schema_privilege(%s, %s, 'CREATE')",
                    (runtime_role, schema_name, runtime_role, schema_name),
                )
                if cursor.fetchone() != (True, False):
                    raise RuntimeError(
                        "Database validation failed: application schema grants are unsafe"
                    )

            read_only_tables = (
                "operations.employees",
                "operations.contractors",
                "public.checkpoint_migrations",
            )
            for table_name in read_only_tables:
                for privilege in ("SELECT",):
                    cursor.execute(
                        "SELECT has_table_privilege(%s, %s, %s)",
                        (runtime_role, table_name, privilege),
                    )
                    if cursor.fetchone() != (True,):
                        raise RuntimeError(
                            "Database validation failed: read-only grant is missing"
                        )
                for privilege in ("INSERT", "UPDATE", "DELETE", "TRUNCATE"):
                    cursor.execute(
                        "SELECT has_table_privilege(%s, %s, %s)",
                        (runtime_role, table_name, privilege),
                    )
                    if cursor.fetchone() != (False,):
                        raise RuntimeError(
                            "Database validation failed: read-only grant is unsafe"
                        )

            cursor.execute(
                """
                SELECT
                    has_table_privilege(
                        %s, 'operations.access_requests', 'SELECT'
                    ),
                    has_column_privilege(
                        %s, 'operations.access_requests', 'approval_status', 'UPDATE'
                    ),
                    has_column_privilege(
                        %s, 'operations.access_requests', 'request_id', 'UPDATE'
                    )
                """,
                (runtime_role, runtime_role, runtime_role),
            )
            if cursor.fetchone() != (True, True, False):
                raise RuntimeError(
                    "Database validation failed: request update grant is unsafe"
                )
            for privilege in ("INSERT", "UPDATE", "DELETE", "TRUNCATE"):
                cursor.execute(
                    "SELECT has_table_privilege("
                    "%s, 'operations.access_requests', %s)",
                    (runtime_role, privilege),
                )
                if cursor.fetchone() != (False,):
                    raise RuntimeError(
                        "Database validation failed: request table grant is unsafe"
                    )

            read_write_tables = (
                "security.approval_sessions",
                "security.execution_ledger",
                "public.checkpoint_blobs",
                "public.checkpoint_writes",
                "public.checkpoints",
                "public.policy_chunks",
            )
            for table_name in read_write_tables:
                for privilege in ("SELECT", "INSERT", "UPDATE", "DELETE"):
                    cursor.execute(
                        "SELECT has_table_privilege(%s, %s, %s)",
                        (runtime_role, table_name, privilege),
                    )
                    if cursor.fetchone() != (True,):
                        raise RuntimeError(
                            "Database validation failed: runtime DML grant is missing"
                        )
                for privilege in ("TRUNCATE", "REFERENCES", "TRIGGER"):
                    cursor.execute(
                        "SELECT has_table_privilege(%s, %s, %s)",
                        (runtime_role, table_name, privilege),
                    )
                    if cursor.fetchone() != (False,):
                        raise RuntimeError(
                            "Database validation failed: runtime DML grant is unsafe"
                        )

            if expect_demo_data:
                for table_name, expected_count in EXPECTED_DEMO_COUNTS.items():
                    cursor.execute(
                        f"SELECT count(*) FROM operations.{table_name}"
                    )
                    if cursor.fetchone() != (expected_count,):
                        raise RuntimeError(
                            "Database validation failed: demo row count is unexpected"
                        )

    return vector_version


def run(*, seed_demo_data: bool) -> MigrationResult:
    source = PostgresConfig.from_env()
    config = _database_config(source)
    runtime_role = os.environ.get("POSTGRES_APPLICATION_ROLE", "").strip()
    runtime_password = os.environ.get("POSTGRES_APPLICATION_PASSWORD", "")
    if not runtime_role:
        raise RuntimeError("POSTGRES_APPLICATION_ROLE is required")
    if not runtime_password:
        raise RuntimeError("POSTGRES_APPLICATION_PASSWORD is required")

    # A session advisory lock serializes manual job starts. The migration is
    # intentionally never called by API startup or the steady-state CD job.
    with psycopg.connect(**config.connect_kwargs, autocommit=True) as lock_connection:
        with lock_connection.cursor() as cursor:
            cursor.execute("SELECT pg_advisory_lock(hashtext(%s))", (LOCK_NAME,))
        try:
            _ensure_migration_ledger(config)
            applied = _apply_current_migration(
                config,
                runtime_role,
                runtime_password,
            )
            grant_runtime_database_role(config, runtime_role)
            if seed_demo_data:
                setup_operations_database(config)  # type: ignore[arg-type]
            vector_version = _validate_postconditions(
                config,
                runtime_role,
                expect_demo_data=seed_demo_data,
            )
        finally:
            with lock_connection.cursor() as cursor:
                cursor.execute("SELECT pg_advisory_unlock(hashtext(%s))", (LOCK_NAME,))
    return MigrationResult(applied=applied, vector_version=vector_version)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--seed-demo-data",
        action="store_true",
        help="Idempotently load only the repository's fictional operations records.",
    )
    args = parser.parse_args()
    try:
        result = run(seed_demo_data=args.seed_demo_data)
    except Exception:
        # Driver exceptions can embed DSNs or connection details. Keep job output
        # deliberately generic and diagnose with metadata-only checks.
        print("Database migration or validation failed.", file=sys.stderr)
        return 1

    state = "applied" if result.applied else "already current"
    print(f"Database migration {MIGRATION_VERSION} {state}.")
    if args.seed_demo_data:
        print("Fictional portfolio records are initialized and validated.")
    print(f"Vector extension version: {result.vector_version}")
    print("Database validation successful.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
