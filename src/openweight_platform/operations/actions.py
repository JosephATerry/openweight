"""Controlled writes for fictional operational access requests."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Literal, TypeAlias, cast

import psycopg
from psycopg.types.json import Jsonb

from openweight_platform.rag.database import PostgresConfig


AccessRequestStatus: TypeAlias = Literal["approved", "pending", "denied"]

ALLOWED_ACCESS_REQUEST_STATUSES = frozenset(
    {
        "approved",
        "pending",
        "denied",
    }
)

SET_ACCESS_REQUEST_STATUS_QUERY = """
    WITH current_request AS (
        SELECT
            request_id,
            approval_status AS previous_status
        FROM operations.access_requests
        WHERE request_id = %s
        FOR UPDATE
    ),
    updated_request AS (
        UPDATE operations.access_requests AS target
        SET approval_status = %s
        FROM current_request
        WHERE target.request_id = current_request.request_id
        RETURNING
            target.request_id,
            target.approval_status AS new_status
    )
    SELECT
        updated_request.request_id,
        current_request.previous_status,
        updated_request.new_status
    FROM updated_request
    JOIN current_request USING (request_id)
"""

CLAIM_EXECUTION_QUERY = """
    INSERT INTO security.execution_ledger (
        effect_id,
        action_type,
        access_request_id,
        requested_status,
        state
    ) VALUES (%s, 'set_access_request_status', %s, %s, 'claimed')
    ON CONFLICT (effect_id) DO NOTHING
    RETURNING effect_id
"""

READ_EXECUTION_QUERY = """
    SELECT action_type, access_request_id, requested_status, state, result
    FROM security.execution_ledger
    WHERE effect_id = %s
    FOR UPDATE
"""

COMPLETE_EXECUTION_QUERY = """
    UPDATE security.execution_ledger
    SET state = 'completed', result = %s, completed_at = CURRENT_TIMESTAMP
    WHERE effect_id = %s AND state = 'claimed'
"""


class AccessRequestNotFoundError(LookupError):
    """Raised when the exact fictional access request does not exist."""


class UnsupportedAccessRequestStatusError(ValueError):
    """Raised when a requested status is outside the operations domain."""


class IdempotencyConflictError(RuntimeError):
    """Raised if an effect key is reused for a different or incomplete action."""


@dataclass(frozen=True)
class AccessRequestStatusChange:
    request_id: str
    previous_status: str
    new_status: AccessRequestStatus
    changed: bool


ConnectionFactory = Callable[..., Any]


def validate_access_request_status(value: object) -> AccessRequestStatus:
    """Return a known access-request status or fail before database work."""
    if not isinstance(value, str) or value not in ALLOWED_ACCESS_REQUEST_STATUSES:
        allowed = ", ".join(sorted(ALLOWED_ACCESS_REQUEST_STATUSES))
        raise UnsupportedAccessRequestStatusError(
            f"new_status must be one of: {allowed}"
        )
    return cast(AccessRequestStatus, value)


def _validate_request_id(request_id: str) -> None:
    if not isinstance(request_id, str) or not request_id.strip():
        raise ValueError("request_id must be a non-empty string")


def _validate_effect_id(effect_id: str) -> None:
    if not isinstance(effect_id, str) or not effect_id.strip() or len(effect_id) > 128:
        raise ValueError("effect_id must be a bounded non-empty string")


class AccessRequestActionService:
    """Perform the single allowed access-request status write."""

    def __init__(
        self,
        config: PostgresConfig,
        *,
        connection_factory: ConnectionFactory | None = None,
    ) -> None:
        self._config = config
        self._connection_factory = connection_factory or psycopg.connect

    def set_access_request_status(
        self,
        request_id: str,
        new_status: str,
    ) -> AccessRequestStatusChange:
        """Set one exact request's status and return its before/after state.

        Setting the current status again is allowed and returns ``changed=False``.
        """
        _validate_request_id(request_id)
        validated_status = validate_access_request_status(new_status)

        with self._connection_factory(
            **self._config.connect_kwargs
        ) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    SET_ACCESS_REQUEST_STATUS_QUERY,
                    (request_id, validated_status),
                )
                row = cursor.fetchone()

                if row is None:
                    raise AccessRequestNotFoundError(
                        f"Access request {request_id!r} does not exist"
                    )

                returned_request_id, previous_status, returned_status = row
                if (
                    returned_request_id != request_id
                    or returned_status != validated_status
                ):
                    raise RuntimeError(
                        "Database returned an inconsistent status update"
                    )

                return AccessRequestStatusChange(
                    request_id=request_id,
                    previous_status=previous_status,
                    new_status=validated_status,
                    changed=previous_status != validated_status,
                )

    def set_access_request_status_once(
        self,
        effect_id: str,
        request_id: str,
        new_status: str,
    ) -> AccessRequestStatusChange:
        """Atomically execute or replay one exact controlled status effect."""
        _validate_effect_id(effect_id)
        _validate_request_id(request_id)
        validated_status = validate_access_request_status(new_status)

        with self._connection_factory(**self._config.connect_kwargs) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    CLAIM_EXECUTION_QUERY,
                    (effect_id, request_id, validated_status),
                )
                claimed = cursor.fetchone() is not None
                if not claimed:
                    cursor.execute(READ_EXECUTION_QUERY, (effect_id,))
                    existing = cursor.fetchone()
                    if existing is None:
                        raise IdempotencyConflictError(
                            "The execution claim could not be recovered"
                        )
                    action_type, stored_request_id, stored_status, state, result = existing
                    if (
                        action_type != "set_access_request_status"
                        or stored_request_id != request_id
                        or stored_status != validated_status
                        or state != "completed"
                        or not isinstance(result, dict)
                    ):
                        raise IdempotencyConflictError(
                            "The effect identifier conflicts with another action"
                        )
                    return _status_change_from_result(
                        result,
                        expected_request_id=request_id,
                        expected_status=validated_status,
                    )

                cursor.execute(
                    SET_ACCESS_REQUEST_STATUS_QUERY,
                    (request_id, validated_status),
                )
                row = cursor.fetchone()
                if row is None:
                    raise AccessRequestNotFoundError(
                        f"Access request {request_id!r} does not exist"
                    )
                returned_request_id, previous_status, returned_status = row
                if returned_request_id != request_id or returned_status != validated_status:
                    raise RuntimeError(
                        "Database returned an inconsistent status update"
                    )
                change = AccessRequestStatusChange(
                    request_id=request_id,
                    previous_status=previous_status,
                    new_status=validated_status,
                    changed=previous_status != validated_status,
                )
                cursor.execute(
                    COMPLETE_EXECUTION_QUERY,
                    (Jsonb(
                        {
                            "request_id": change.request_id,
                            "previous_status": change.previous_status,
                            "new_status": change.new_status,
                            "changed": change.changed,
                        }
                    ), effect_id),
                )
                if cursor.rowcount != 1:
                    raise IdempotencyConflictError(
                        "The execution claim was not completed"
                    )
                return change


def _status_change_from_result(
    result: dict[str, object],
    *,
    expected_request_id: str,
    expected_status: AccessRequestStatus,
) -> AccessRequestStatusChange:
    if set(result) != {"request_id", "previous_status", "new_status", "changed"}:
        raise IdempotencyConflictError("Stored execution result is malformed")
    if (
        result["request_id"] != expected_request_id
        or result["new_status"] != expected_status
        or not isinstance(result["previous_status"], str)
        or not isinstance(result["changed"], bool)
    ):
        raise IdempotencyConflictError("Stored execution result is inconsistent")
    return AccessRequestStatusChange(
        request_id=expected_request_id,
        previous_status=result["previous_status"],
        new_status=expected_status,
        changed=result["changed"],
    )


__all__ = [
    "ALLOWED_ACCESS_REQUEST_STATUSES",
    "CLAIM_EXECUTION_QUERY",
    "COMPLETE_EXECUTION_QUERY",
    "AccessRequestActionService",
    "AccessRequestNotFoundError",
    "AccessRequestStatus",
    "AccessRequestStatusChange",
    "IdempotencyConflictError",
    "READ_EXECUTION_QUERY",
    "SET_ACCESS_REQUEST_STATUS_QUERY",
    "UnsupportedAccessRequestStatusError",
    "validate_access_request_status",
]
