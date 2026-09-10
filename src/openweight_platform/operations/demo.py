"""Process-local, synthetic-only operations state for the public demo profile."""

from __future__ import annotations

import threading
from dataclasses import replace
from typing import TypeVar

from openweight_platform.operations.actions import (
    AccessRequestNotFoundError,
    AccessRequestStatusChange,
    IdempotencyConflictError,
    validate_access_request_status,
)
from openweight_platform.operations.models import AccessRequest, Contractor, Employee
from openweight_platform.operations.seed import ACCESS_REQUESTS, CONTRACTORS, EMPLOYEES


Record = TypeVar("Record", Employee, Contractor)


class DemoOperationsService:
    """Bounded in-memory facade over canonical fictional fixtures.

    State is shared only within one application process and resets on restart.
    The fixed writer and effect ledger still enforce exactly-once semantics for
    that process; no arbitrary table, query, or action interface exists.
    """

    def __init__(self) -> None:
        self._employees = {item.employee_id: replace(item) for item in EMPLOYEES}
        self._contractors = {
            item.contractor_id: replace(item) for item in CONTRACTORS
        }
        self._requests = {item.request_id: replace(item) for item in ACCESS_REQUESTS}
        self._effects: dict[
            str, tuple[str, str, AccessRequestStatusChange]
        ] = {}
        self._lock = threading.RLock()

    def lookup_employee(self, identifier: str) -> Employee | None:
        _validate_identifier(identifier)
        with self._lock:
            direct = self._employees.get(identifier)
            matches = [
                item for item in self._employees.values()
                if item.full_name == identifier
            ]
            return replace(direct) if direct is not None else _unique(matches)

    def lookup_contractor(self, identifier: str) -> Contractor | None:
        _validate_identifier(identifier)
        with self._lock:
            direct = self._contractors.get(identifier)
            matches = [
                item for item in self._contractors.values()
                if item.full_name == identifier
            ]
            return replace(direct) if direct is not None else _unique(matches)

    def lookup_access_request(self, request_id: str) -> AccessRequest | None:
        _validate_identifier(request_id)
        with self._lock:
            item = self._requests.get(request_id)
            return replace(item) if item is not None else None

    def list_access_requests(
        self,
        *,
        approval_status: str | None = None,
        limit: int = 25,
    ) -> list[AccessRequest]:
        if approval_status not in (None, "approved", "pending", "denied"):
            raise ValueError("unsupported approval status")
        if (
            not isinstance(limit, int)
            or isinstance(limit, bool)
            or not 1 <= limit <= 100
        ):
            raise ValueError("limit must be between 1 and 100")
        with self._lock:
            return [
                replace(item)
                for item in sorted(
                    self._requests.values(), key=lambda row: row.request_id
                )
                if approval_status is None or item.approval_status == approval_status
            ][:limit]

    def set_access_request_status_once(
        self,
        effect_id: str,
        request_id: str,
        new_status: str,
    ) -> AccessRequestStatusChange:
        if not effect_id or len(effect_id) > 128:
            raise ValueError("effect_id must be a bounded non-empty string")
        _validate_identifier(request_id)
        status = validate_access_request_status(new_status)
        with self._lock:
            previous = self._effects.get(effect_id)
            if previous is not None:
                stored_request, stored_status, result = previous
                if stored_request != request_id or stored_status != status:
                    raise IdempotencyConflictError(
                        "The effect identifier conflicts with another action"
                    )
                return result
            current = self._requests.get(request_id)
            if current is None:
                raise AccessRequestNotFoundError
            result = AccessRequestStatusChange(
                request_id=request_id,
                previous_status=current.approval_status,
                new_status=status,
                changed=current.approval_status != status,
            )
            self._requests[request_id] = replace(current, approval_status=status)
            self._effects[effect_id] = (request_id, status, result)
            return result


def _validate_identifier(identifier: str) -> None:
    if not isinstance(identifier, str) or not identifier.strip():
        raise ValueError("identifier must be a non-empty string")


def _unique(items: list[Record]) -> Record | None:
    if len(items) > 1:
        raise LookupError("The exact name is ambiguous")
    return replace(items[0]) if items else None


__all__ = ["DemoOperationsService"]
