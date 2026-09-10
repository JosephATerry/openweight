"""Read-only PostgreSQL lookups for operational records."""

from collections.abc import Callable
from typing import Any

import psycopg

from openweight_platform.operations.models import (
    AccessRequest,
    Contractor,
    Employee,
)
from openweight_platform.rag.database import PostgresConfig


EMPLOYEE_BY_ID_QUERY = """
    SELECT
        employee_id,
        full_name,
        employment_status,
        department,
        manager_employee_id,
        mfa_enrolled,
        security_training_current
    FROM operations.employees
    WHERE employee_id = %s
"""

EMPLOYEE_BY_NAME_QUERY = """
    SELECT
        employee_id,
        full_name,
        employment_status,
        department,
        manager_employee_id,
        mfa_enrolled,
        security_training_current
    FROM operations.employees
    WHERE full_name = %s
    LIMIT 2
"""

CONTRACTOR_BY_ID_QUERY = """
    SELECT
        contractor_id,
        full_name,
        vendor_name,
        engagement_status,
        sponsor_employee_id,
        engagement_end_date,
        managed_identity,
        mfa_enrolled
    FROM operations.contractors
    WHERE contractor_id = %s
"""

CONTRACTOR_BY_NAME_QUERY = """
    SELECT
        contractor_id,
        full_name,
        vendor_name,
        engagement_status,
        sponsor_employee_id,
        engagement_end_date,
        managed_identity,
        mfa_enrolled
    FROM operations.contractors
    WHERE full_name = %s
    LIMIT 2
"""

ACCESS_REQUEST_BY_ID_QUERY = """
    SELECT
        request_id,
        subject_type,
        subject_id,
        system_name,
        requested_role,
        approval_status,
        requested_start_date,
        requested_end_date
    FROM operations.access_requests
    WHERE request_id = %s
"""

ACCESS_REQUEST_LIST_QUERY = """
    SELECT
        request_id,
        subject_type,
        subject_id,
        system_name,
        requested_role,
        approval_status,
        requested_start_date,
        requested_end_date
    FROM operations.access_requests
    WHERE (%s::text IS NULL OR approval_status = %s)
    ORDER BY request_id
    LIMIT %s
"""


class AmbiguousLookupError(LookupError):
    """Raised when an exact-name lookup matches multiple records."""


ConnectionFactory = Callable[..., Any]


def _validate_identifier(identifier: str) -> None:
    if not isinstance(identifier, str) or not identifier.strip():
        raise ValueError("identifier must be a non-empty string")


class OperationsLookupService:
    """Perform read-only operations lookups with a PostgreSQL connection."""

    def __init__(
        self,
        config: PostgresConfig,
        *,
        connection_factory: ConnectionFactory | None = None,
    ) -> None:
        self._config = config
        self._connection_factory = connection_factory or psycopg.connect

    def lookup_employee(self, identifier: str) -> Employee | None:
        """Look up an employee by exact ID, then by exact full name."""

        _validate_identifier(identifier)

        with self._connection_factory(
            **self._config.connect_kwargs
        ) as connection:
            with connection.cursor() as cursor:
                cursor.execute(EMPLOYEE_BY_ID_QUERY, (identifier,))
                row = cursor.fetchone()

                if row is not None:
                    return Employee(*row)

                cursor.execute(EMPLOYEE_BY_NAME_QUERY, (identifier,))
                rows = cursor.fetchall()

        if len(rows) > 1:
            raise AmbiguousLookupError(
                f"Multiple employees have the exact full name {identifier!r}"
            )

        return Employee(*rows[0]) if rows else None

    def lookup_contractor(self, identifier: str) -> Contractor | None:
        """Look up a contractor by exact ID, then by exact full name."""

        _validate_identifier(identifier)

        with self._connection_factory(
            **self._config.connect_kwargs
        ) as connection:
            with connection.cursor() as cursor:
                cursor.execute(CONTRACTOR_BY_ID_QUERY, (identifier,))
                row = cursor.fetchone()

                if row is not None:
                    return Contractor(*row)

                cursor.execute(CONTRACTOR_BY_NAME_QUERY, (identifier,))
                rows = cursor.fetchall()

        if len(rows) > 1:
            raise AmbiguousLookupError(
                f"Multiple contractors have the exact full name {identifier!r}"
            )

        return Contractor(*rows[0]) if rows else None

    def lookup_access_request(self, request_id: str) -> AccessRequest | None:
        """Look up an access request by its exact request ID."""

        _validate_identifier(request_id)

        with self._connection_factory(
            **self._config.connect_kwargs
        ) as connection:
            with connection.cursor() as cursor:
                cursor.execute(ACCESS_REQUEST_BY_ID_QUERY, (request_id,))
                row = cursor.fetchone()

        return AccessRequest(*row) if row is not None else None

    def list_access_requests(
        self,
        *,
        approval_status: str | None = None,
        limit: int = 25,
    ) -> list[AccessRequest]:
        """List a bounded, deterministically ordered set of access requests."""

        if approval_status not in (None, "approved", "pending", "denied"):
            raise ValueError("unsupported approval status")
        if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 100:
            raise ValueError("limit must be between 1 and 100")

        with self._connection_factory(
            **self._config.connect_kwargs
        ) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    ACCESS_REQUEST_LIST_QUERY,
                    (approval_status, approval_status, limit),
                )
                rows = cursor.fetchall()

        return [AccessRequest(*row) for row in rows]


def lookup_employee(
    identifier: str,
    *,
    config: PostgresConfig | None = None,
) -> Employee | None:
    """Look up an employee using explicit or environment-based configuration."""

    _validate_identifier(identifier)
    resolved_config = config or PostgresConfig.from_env()
    return OperationsLookupService(resolved_config).lookup_employee(identifier)


def lookup_contractor(
    identifier: str,
    *,
    config: PostgresConfig | None = None,
) -> Contractor | None:
    """Look up a contractor using explicit or environment-based configuration."""

    _validate_identifier(identifier)
    resolved_config = config or PostgresConfig.from_env()
    return OperationsLookupService(resolved_config).lookup_contractor(identifier)


def lookup_access_request(
    request_id: str,
    *,
    config: PostgresConfig | None = None,
) -> AccessRequest | None:
    """Look up an access request using explicit or environment-based configuration."""

    _validate_identifier(request_id)
    resolved_config = config or PostgresConfig.from_env()
    return OperationsLookupService(resolved_config).lookup_access_request(request_id)


__all__ = [
    "AmbiguousLookupError",
    "ACCESS_REQUEST_LIST_QUERY",
    "OperationsLookupService",
    "lookup_access_request",
    "lookup_contractor",
    "lookup_employee",
]
