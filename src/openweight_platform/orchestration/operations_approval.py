"""Approval executor adapter for the controlled operations write."""

from __future__ import annotations

from dataclasses import asdict
from typing import Protocol

from openweight_platform.operations.actions import (
    AccessRequestStatusChange,
    validate_access_request_status,
)
from openweight_platform.orchestration.approval import (
    ActionExecutor,
    ActionProposal,
)


SET_ACCESS_REQUEST_STATUS_ACTION = "set_access_request_status"


class AccessRequestStatusWriter(Protocol):
    def set_access_request_status_once(
        self,
        effect_id: str,
        request_id: str,
        new_status: str,
    ) -> AccessRequestStatusChange: ...


class OperationalActionValidationError(ValueError):
    """Raised when an action proposal is not the one supported write."""


def build_access_request_action_executor(
    service: AccessRequestStatusWriter,
) -> ActionExecutor:
    """Adapt one approved action proposal to the narrow write service."""

    def execute(proposal: ActionProposal, effect_id: str) -> object:
        if proposal.action_type != SET_ACCESS_REQUEST_STATUS_ACTION:
            raise OperationalActionValidationError(
                "Unsupported operational action_type: "
                f"{proposal.action_type!r}"
            )

        expected_arguments = {"request_id", "new_status"}
        if set(proposal.arguments) != expected_arguments:
            raise OperationalActionValidationError(
                "set_access_request_status arguments must contain exactly "
                "'request_id' and 'new_status'"
            )

        request_id = proposal.arguments["request_id"]
        if not isinstance(request_id, str) or not request_id.strip():
            raise OperationalActionValidationError(
                "request_id must be a non-empty string"
            )

        try:
            new_status = validate_access_request_status(
                proposal.arguments["new_status"]
            )
        except ValueError as error:
            raise OperationalActionValidationError(str(error)) from error

        result = service.set_access_request_status_once(
            effect_id,
            request_id,
            new_status,
        )
        return asdict(result)

    return execute


__all__ = [
    "AccessRequestStatusWriter",
    "OperationalActionValidationError",
    "SET_ACCESS_REQUEST_STATUS_ACTION",
    "build_access_request_action_executor",
]
