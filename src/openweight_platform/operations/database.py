"""Idempotent PostgreSQL schema setup for operational records."""

import psycopg

from openweight_platform.operations.seed import (
    access_request_seed_rows,
    contractor_seed_rows,
    employee_seed_rows,
    operations_seed_counts,
)
from openweight_platform.rag.database import PostgresConfig


OPERATIONS_SCHEMA = "operations"

SCHEMA_STATEMENTS = (
    "CREATE SCHEMA IF NOT EXISTS operations",
    """
    CREATE TABLE IF NOT EXISTS operations.employees (
        employee_id TEXT PRIMARY KEY,
        full_name TEXT NOT NULL,
        employment_status TEXT NOT NULL,
        department TEXT NOT NULL,
        manager_employee_id TEXT,
        mfa_enrolled BOOLEAN NOT NULL,
        security_training_current BOOLEAN NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS operations.contractors (
        contractor_id TEXT PRIMARY KEY,
        full_name TEXT NOT NULL,
        vendor_name TEXT NOT NULL,
        engagement_status TEXT NOT NULL,
        sponsor_employee_id TEXT NOT NULL,
        engagement_end_date DATE NOT NULL,
        managed_identity BOOLEAN NOT NULL,
        mfa_enrolled BOOLEAN NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS operations.access_requests (
        request_id TEXT PRIMARY KEY,
        subject_type TEXT NOT NULL,
        subject_id TEXT NOT NULL,
        system_name TEXT NOT NULL,
        requested_role TEXT NOT NULL,
        approval_status TEXT NOT NULL,
        requested_start_date DATE NOT NULL,
        requested_end_date DATE
    )
    """,
)

UPSERT_EMPLOYEES = """
    INSERT INTO operations.employees (
        employee_id,
        full_name,
        employment_status,
        department,
        manager_employee_id,
        mfa_enrolled,
        security_training_current
    ) VALUES (%s, %s, %s, %s, %s, %s, %s)
    ON CONFLICT (employee_id) DO UPDATE SET
        full_name = EXCLUDED.full_name,
        employment_status = EXCLUDED.employment_status,
        department = EXCLUDED.department,
        manager_employee_id = EXCLUDED.manager_employee_id,
        mfa_enrolled = EXCLUDED.mfa_enrolled,
        security_training_current = EXCLUDED.security_training_current
"""

UPSERT_CONTRACTORS = """
    INSERT INTO operations.contractors (
        contractor_id,
        full_name,
        vendor_name,
        engagement_status,
        sponsor_employee_id,
        engagement_end_date,
        managed_identity,
        mfa_enrolled
    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
    ON CONFLICT (contractor_id) DO UPDATE SET
        full_name = EXCLUDED.full_name,
        vendor_name = EXCLUDED.vendor_name,
        engagement_status = EXCLUDED.engagement_status,
        sponsor_employee_id = EXCLUDED.sponsor_employee_id,
        engagement_end_date = EXCLUDED.engagement_end_date,
        managed_identity = EXCLUDED.managed_identity,
        mfa_enrolled = EXCLUDED.mfa_enrolled
"""

UPSERT_ACCESS_REQUESTS = """
    INSERT INTO operations.access_requests (
        request_id,
        subject_type,
        subject_id,
        system_name,
        requested_role,
        approval_status,
        requested_start_date,
        requested_end_date
    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
    ON CONFLICT (request_id) DO UPDATE SET
        subject_type = EXCLUDED.subject_type,
        subject_id = EXCLUDED.subject_id,
        system_name = EXCLUDED.system_name,
        requested_role = EXCLUDED.requested_role,
        approval_status = EXCLUDED.approval_status,
        requested_start_date = EXCLUDED.requested_start_date,
        requested_end_date = EXCLUDED.requested_end_date
"""


def setup_operations_database(config: PostgresConfig) -> dict[str, int]:
    """Create operations tables and idempotently upsert their fictional seeds."""

    with psycopg.connect(**config.connect_kwargs) as connection:
        with connection.cursor() as cursor:
            for statement in SCHEMA_STATEMENTS:
                cursor.execute(statement)

            cursor.executemany(UPSERT_EMPLOYEES, employee_seed_rows())
            cursor.executemany(UPSERT_CONTRACTORS, contractor_seed_rows())
            cursor.executemany(UPSERT_ACCESS_REQUESTS, access_request_seed_rows())

    return operations_seed_counts()
