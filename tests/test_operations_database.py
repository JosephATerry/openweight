from datetime import date

from openweight_platform.operations.database import (
    SCHEMA_STATEMENTS,
    UPSERT_ACCESS_REQUESTS,
    UPSERT_CONTRACTORS,
    UPSERT_EMPLOYEES,
)
from openweight_platform.operations.seed import (
    ACCESS_REQUESTS,
    CONTRACTORS,
    EMPLOYEES,
    access_request_seed_rows,
    contractor_seed_rows,
    employee_seed_rows,
    operations_seed_counts,
)


def normalized_sql(statement: str) -> str:
    return " ".join(statement.split())


def test_schema_statements_define_operations_tables():
    schema_sql = " ".join(normalized_sql(statement) for statement in SCHEMA_STATEMENTS)

    assert "CREATE SCHEMA IF NOT EXISTS operations" in schema_sql
    assert "CREATE TABLE IF NOT EXISTS operations.employees" in schema_sql
    assert "employee_id TEXT PRIMARY KEY" in schema_sql
    assert "manager_employee_id TEXT" in schema_sql
    assert "CREATE TABLE IF NOT EXISTS operations.contractors" in schema_sql
    assert "engagement_end_date DATE NOT NULL" in schema_sql
    assert "CREATE TABLE IF NOT EXISTS operations.access_requests" in schema_sql
    assert "requested_end_date DATE" in schema_sql


def test_seed_counts_and_ids_are_deterministic_and_unique():
    assert operations_seed_counts() == {
        "employees": 6,
        "contractors": 4,
        "access_requests": 6,
    }
    assert len({employee.employee_id for employee in EMPLOYEES}) == 6
    assert len({contractor.contractor_id for contractor in CONTRACTORS}) == 4
    assert len({request.request_id for request in ACCESS_REQUESTS}) == 6
    assert employee_seed_rows() == employee_seed_rows()
    assert contractor_seed_rows() == contractor_seed_rows()
    assert access_request_seed_rows() == access_request_seed_rows()


def test_employee_seed_has_required_variation():
    assert {employee.employment_status for employee in EMPLOYEES} == {
        "active",
        "inactive",
    }
    assert {employee.mfa_enrolled for employee in EMPLOYEES} == {True, False}
    assert {employee.security_training_current for employee in EMPLOYEES} == {
        True,
        False,
    }


def test_contractor_and_request_seeds_have_required_variation():
    assert {contractor.engagement_status for contractor in CONTRACTORS} == {
        "active",
        "ended",
    }
    assert {request.approval_status for request in ACCESS_REQUESTS} == {
        "approved",
        "pending",
        "denied",
    }
    assert all(
        isinstance(contractor.engagement_end_date, date)
        for contractor in CONTRACTORS
    )


def test_seed_upserts_are_idempotent_by_primary_key():
    assert "ON CONFLICT (employee_id) DO UPDATE" in normalized_sql(UPSERT_EMPLOYEES)
    assert "ON CONFLICT (contractor_id) DO UPDATE" in normalized_sql(UPSERT_CONTRACTORS)
    assert "ON CONFLICT (request_id) DO UPDATE" in normalized_sql(
        UPSERT_ACCESS_REQUESTS
    )
