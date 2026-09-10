#!/usr/bin/env python3
"""Create and seed the fictional operations schema in PostgreSQL."""

from openweight_platform.operations.database import (
    OPERATIONS_SCHEMA,
    setup_operations_database,
)
from openweight_platform.rag.database import PostgresConfig


def main() -> int:
    config = PostgresConfig.from_env()
    counts = setup_operations_database(config)

    print(f"Schema: {OPERATIONS_SCHEMA}")
    for table_name, row_count in counts.items():
        print(f"{table_name}: {row_count} seed rows")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
