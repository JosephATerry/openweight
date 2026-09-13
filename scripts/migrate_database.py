#!/usr/bin/env python3
"""Apply explicit, additive OpenWeight PostgreSQL migrations."""

from __future__ import annotations

import argparse
import asyncio
import os

import psycopg
from langgraph.checkpoint.postgres import PostgresSaver

from openweight_platform.api.config import DatabaseConfig
from openweight_platform.operations.database import (
    setup_operations_database,
    setup_operations_schema,
)
from openweight_platform.rag.database import PostgresConfig
from openweight_platform.rag.vectorstore import initialize_policy_vector_table
from openweight_platform.security.persistence import (
    ensure_runtime_database_role,
    grant_runtime_database_role,
    setup_security_database,
)


MIGRATION_VERSION = 1
MIGRATION_NAME = "azure_initial_schema"
LOCK_NAME = "openweight-database-migrations"


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


def run(*, seed_demo_data: bool) -> None:
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
            ensure_runtime_database_role(config, runtime_role, runtime_password)
            if not _is_applied(config):
                _apply_schema(config)
                _record_migration(config)
            grant_runtime_database_role(config, runtime_role)
            if seed_demo_data:
                setup_operations_database(config)  # type: ignore[arg-type]
        finally:
            with lock_connection.cursor() as cursor:
                cursor.execute("SELECT pg_advisory_unlock(hashtext(%s))", (LOCK_NAME,))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--seed-demo-data",
        action="store_true",
        help="Idempotently load only the repository's fictional operations records.",
    )
    args = parser.parse_args()
    run(seed_demo_data=args.seed_demo_data)
    print(f"Database migration {MIGRATION_VERSION} is applied.")
    if args.seed_demo_data:
        print("Fictional portfolio records are initialized.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
