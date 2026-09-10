#!/usr/bin/env python3
"""Bootstrap durable workflow tables and optionally grant a runtime role."""

from __future__ import annotations

import os

from langgraph.checkpoint.postgres import PostgresSaver

from openweight_platform.api.config import DatabaseConfig
from openweight_platform.rag.database import PostgresConfig
from openweight_platform.security.persistence import (
    grant_runtime_database_role,
    setup_security_database,
)


def main() -> int:
    source = PostgresConfig.from_env()
    config = DatabaseConfig(
        database=source.database,
        user=source.user,
        password=source.password,
        host=source.host,
        port=source.port,
        sslmode=source.sslmode,
        sslrootcert=source.sslrootcert,
    )
    setup_security_database(config)
    with PostgresSaver.from_conn_string(config.conninfo) as saver:
        saver.setup()

    runtime_role = os.environ.get("POSTGRES_APPLICATION_ROLE", "").strip()
    if runtime_role:
        grant_runtime_database_role(config, runtime_role)

    print("Durable approval, checkpoint, and idempotency schemas are ready.")
    if runtime_role:
        print("Least-privilege runtime grants were applied.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
