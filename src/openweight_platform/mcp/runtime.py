"""Thin MCP tool service over the existing application runtime."""

from __future__ import annotations

import logging
import time
from collections.abc import Awaitable, Callable
from typing import TypeVar

from mcp.server.mcpserver.exceptions import ToolError
from pydantic import BaseModel

from openweight_platform.api.errors import (
    AuthenticationRequiredError,
    DependencyUnavailableError,
    InvalidRequestError,
    PermissionDeniedError,
    ResourceNotFoundError,
    ServiceError,
    StateConflictError,
    UnsafeResultError,
)
from openweight_platform.api.observability import Observability, elapsed_seconds
from openweight_platform.api.request_context import current_request_id
from openweight_platform.api.runtime import PlatformRuntime, safe_json_value
from openweight_platform.api.security import Permission
from openweight_platform.mcp.contracts import (
    AccessRequestListResult,
    AccessRequestLookupResult,
    AccessRequestRecord,
    ApprovalProposalResult,
    ApprovalResumeResult,
    ContractorLookupResult,
    ContractorRecord,
    EmployeeLookupResult,
    EmployeeRecord,
    PolicyMatch,
    PolicySearchResult,
)
from openweight_platform.mcp.security import McpAuthorization
from openweight_platform.orchestration.approval import ApprovalDecision


T = TypeVar("T")
RuntimeProvider = Callable[[], PlatformRuntime]


def _error_type(error: ServiceError) -> str:
    if isinstance(error, AuthenticationRequiredError):
        return "authentication_error"
    if isinstance(error, PermissionDeniedError):
        return "authorization_error"
    if isinstance(error, InvalidRequestError):
        return "validation_error"
    if isinstance(error, ResourceNotFoundError):
        return "not_found"
    if isinstance(error, StateConflictError):
        return "approval_conflict"
    if isinstance(error, DependencyUnavailableError):
        return "dependency_unavailable"
    if isinstance(error, UnsafeResultError):
        return "unsafe_result"
    return "internal_error"


def _safe_tool_error(error: ServiceError) -> ToolError:
    return ToolError(_error_type(error))


def _validate(model: type[T], value: object) -> T:
    if not issubclass(model, BaseModel):
        raise TypeError("MCP result contract must be a Pydantic model")
    return model.model_validate(value, strict=False)  # type: ignore[return-value]


class McpToolService:
    """Authorize, observe, and delegate MCP tools without business duplication."""

    def __init__(
        self,
        runtime_provider: RuntimeProvider,
        authorization: McpAuthorization,
        observability: Observability,
        logger: logging.Logger,
    ) -> None:
        self._runtime_provider = runtime_provider
        self._authorization = authorization
        self._observability = observability
        self._logger = logger

    async def _execute(
        self,
        *,
        tool_name: str,
        permission: Permission,
        call: Callable[[str], Awaitable[T]],
    ) -> T:
        request_id = current_request_id()
        started = time.perf_counter()
        with self._observability.span(
            "mcp.tool",
            {
                "openweight.operation": "mcp",
                "openweight.protocol": "mcp",
                "openweight.request_id": request_id,
                "openweight.tool_name": tool_name,
            },
        ) as span:
            try:
                self._authorization.require(permission)
                result = await call(request_id)
            except ServiceError as error:
                error_type = _error_type(error)
                self._observability.record_mcp(
                    tool_name=tool_name,
                    result="error",
                    seconds=elapsed_seconds(started),
                )
                self._observability.record_error(
                    operation="mcp",
                    error_type=error_type,
                )
                self._observability.mark_span_error(span, error_type)
                self._logger.warning(
                    "mcp_tool_rejected",
                    extra={
                        "request_id": request_id,
                        "operation": "mcp",
                        "tool_name": tool_name,
                        "result": "error",
                        "error_type": error_type,
                    },
                )
                raise _safe_tool_error(error) from error
            except Exception:
                self._observability.record_mcp(
                    tool_name=tool_name,
                    result="error",
                    seconds=elapsed_seconds(started),
                )
                self._observability.record_error(
                    operation="mcp",
                    error_type="internal_error",
                )
                self._observability.mark_span_error(span, "internal_error")
                self._logger.error(
                    "mcp_tool_failed",
                    extra={
                        "request_id": request_id,
                        "operation": "mcp",
                        "tool_name": tool_name,
                        "result": "error",
                        "error_type": "internal_error",
                    },
                )
                raise
            self._observability.set_span_attributes(
                span,
                {"openweight.result": "success"},
            )
        self._observability.record_mcp(
            tool_name=tool_name,
            result="success",
            seconds=elapsed_seconds(started),
        )
        trace_context = self._observability.trace_context()
        self._logger.info(
            "mcp_tool_completed",
            extra={
                "request_id": request_id,
                "operation": "mcp",
                "tool_name": tool_name,
                "result": "success",
                "trace_id": trace_context.trace_id,
                "span_id": trace_context.span_id,
            },
        )
        return result

    async def search_policy(self, query: str, limit: int) -> PolicySearchResult:
        async def invoke(request_id: str) -> PolicySearchResult:
            matches = await self._runtime_provider().search_policy(
                query,
                limit=limit,
                request_id=request_id,
            )
            return PolicySearchResult(
                request_id=request_id,
                matches=[_validate(PolicyMatch, match) for match in matches],
            )

        return await self._execute(
            tool_name="search_policy",
            permission="agent.query",
            call=invoke,
        )

    async def lookup_employee(self, identifier: str) -> EmployeeLookupResult:
        async def invoke(request_id: str) -> EmployeeLookupResult:
            record = await self._runtime_provider().lookup_employee(
                identifier,
                request_id=request_id,
            )
            return EmployeeLookupResult(
                request_id=request_id,
                employee=_validate(EmployeeRecord, record),
            )

        return await self._execute(
            tool_name="lookup_employee",
            permission="agent.query",
            call=invoke,
        )

    async def lookup_contractor(self, identifier: str) -> ContractorLookupResult:
        async def invoke(request_id: str) -> ContractorLookupResult:
            record = await self._runtime_provider().lookup_contractor(
                identifier,
                request_id=request_id,
            )
            return ContractorLookupResult(
                request_id=request_id,
                contractor=_validate(ContractorRecord, record),
            )

        return await self._execute(
            tool_name="lookup_contractor",
            permission="agent.query",
            call=invoke,
        )

    async def lookup_access_request(
        self,
        access_request_id: str,
    ) -> AccessRequestLookupResult:
        async def invoke(request_id: str) -> AccessRequestLookupResult:
            record = await self._runtime_provider().lookup_access_request(
                access_request_id,
                request_id=request_id,
            )
            return AccessRequestLookupResult(
                request_id=request_id,
                access_request=_validate(AccessRequestRecord, record),
            )

        return await self._execute(
            tool_name="lookup_access_request",
            permission="agent.query",
            call=invoke,
        )

    async def list_access_requests(
        self,
        approval_status: str | None,
        limit: int,
    ) -> AccessRequestListResult:
        async def invoke(request_id: str) -> AccessRequestListResult:
            records = await self._runtime_provider().list_access_requests(
                approval_status=approval_status,
                limit=limit,
                request_id=request_id,
            )
            return AccessRequestListResult(
                request_id=request_id,
                access_requests=[
                    _validate(AccessRequestRecord, record) for record in records
                ],
            )

        return await self._execute(
            tool_name="list_access_requests",
            permission="agent.query",
            call=invoke,
        )

    async def propose_access_request_status(
        self,
        access_request_id: str,
        new_status: str,
    ) -> ApprovalProposalResult:
        async def invoke(request_id: str) -> ApprovalProposalResult:
            pending = await self._runtime_provider().propose_access_request_status(
                access_request_id,
                new_status,
                request_id=request_id,
            )
            return ApprovalProposalResult(
                request_id=request_id,
                approval_id=pending.approval_id,
                proposal=pending.proposal,
            )

        return await self._execute(
            tool_name="propose_access_request_status",
            permission="actions.propose",
            call=invoke,
        )

    async def resume_access_request_approval(
        self,
        approval_id: str,
        decision: str,
        comment: str | None,
    ) -> ApprovalResumeResult:
        async def invoke(request_id: str) -> ApprovalResumeResult:
            outcome = await self._runtime_provider().resume_approval(
                approval_id,
                ApprovalDecision(decision=decision, comment=comment),  # type: ignore[arg-type]
                request_id=request_id,
            )
            return ApprovalResumeResult(
                request_id=request_id,
                approval_id=outcome.approval_id,
                status=outcome.status,
                action_result=safe_json_value(outcome.action_result),
            )

        return await self._execute(
            tool_name="resume_access_request_approval",
            permission="approvals.resume",
            call=invoke,
        )


__all__ = ["McpToolService", "RuntimeProvider"]
