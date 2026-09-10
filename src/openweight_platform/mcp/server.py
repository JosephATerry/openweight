"""Official MCP 2026-07-28 server mounted over shared platform services."""

from __future__ import annotations

import logging
from typing import Annotated, Literal

from mcp.server import MCPServer
from mcp.server.caching import CacheHint
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import ToolAnnotations
from pydantic import Field
from starlette.applications import Starlette

from openweight_platform.api.config import ServiceSettings
from openweight_platform.api.logging import configure_api_logging
from openweight_platform.api.observability import Observability
from openweight_platform.api.security import AuthorizationBoundary
from openweight_platform.mcp.contracts import (
    AccessRequestListResult,
    AccessRequestLookupResult,
    ApprovalProposalResult,
    ApprovalResumeResult,
    ContractorLookupResult,
    EmployeeLookupResult,
    PolicySearchResult,
)
from openweight_platform.mcp.runtime import McpToolService, RuntimeProvider
from openweight_platform.mcp.security import (
    McpAuthorization,
    McpTokenVerifier,
    sdk_auth_settings,
)


MCP_PROTOCOL_REVISION = "2026-07-28"
MCP_SDK_PACKAGE = "mcp"
MCP_SDK_VERSION = "2.1.1"


READ_ONLY = ToolAnnotations(
    readOnlyHint=True,
    destructiveHint=False,
    idempotentHint=True,
    openWorldHint=False,
)
APPROVAL_PROPOSAL = ToolAnnotations(
    readOnlyHint=False,
    destructiveHint=False,
    idempotentHint=False,
    openWorldHint=False,
)
APPROVAL_RESUME = ToolAnnotations(
    readOnlyHint=False,
    destructiveHint=True,
    idempotentHint=True,
    openWorldHint=False,
)


def create_mcp_server(
    *,
    settings: ServiceSettings,
    runtime_provider: RuntimeProvider,
    observability: Observability,
    authorization_boundary: AuthorizationBoundary,
    logger: logging.Logger | None = None,
) -> MCPServer:
    """Build the stable, deterministic MCP catalog without loading a model."""

    tool_service = McpToolService(
        runtime_provider,
        McpAuthorization(enabled=settings.auth_enabled),
        observability,
        logger or configure_api_logging(settings.log_level),
    )
    server = MCPServer(
        name=settings.mcp_server_name,
        title="OpenWeight Enterprise Agent",
        description=(
            "Bounded, authorized interoperability tools over the OpenWeight "
            "platform's existing read and durable approval services."
        ),
        instructions=(
            "Use read-only tools for fictional operational and policy lookups. "
            "Consequential status changes require a proposal followed by a "
            "separate explicit approval-resume call."
        ),
        version=settings.service_version,
        token_verifier=(
            McpTokenVerifier(authorization_boundary)
            if settings.auth_enabled
            else None
        ),
        auth=sdk_auth_settings(settings),
        cache_hints={
            "server/discover": CacheHint(ttl_ms=60_000, scope="private"),
            "tools/list": CacheHint(ttl_ms=60_000, scope="private"),
        },
    )

    @server.tool(
        name="search_policy",
        description="Search the indexed enterprise policy corpus.",
        annotations=READ_ONLY,
    )
    async def search_policy(
        query: Annotated[str, Field(min_length=1, max_length=2_000)],
        limit: Annotated[int, Field(ge=1, le=10)] = 4,
    ) -> PolicySearchResult:
        return await tool_service.search_policy(query, limit)

    @server.tool(
        name="lookup_employee",
        description="Look up one fictional employee by exact ID or full name.",
        annotations=READ_ONLY,
    )
    async def lookup_employee(
        identifier: Annotated[str, Field(min_length=1, max_length=200)],
    ) -> EmployeeLookupResult:
        return await tool_service.lookup_employee(identifier)

    @server.tool(
        name="lookup_contractor",
        description="Look up one fictional contractor by exact ID or full name.",
        annotations=READ_ONLY,
    )
    async def lookup_contractor(
        identifier: Annotated[str, Field(min_length=1, max_length=200)],
    ) -> ContractorLookupResult:
        return await tool_service.lookup_contractor(identifier)

    @server.tool(
        name="lookup_access_request",
        description="Look up one fictional access request by exact request ID.",
        annotations=READ_ONLY,
    )
    async def lookup_access_request(
        access_request_id: Annotated[str, Field(min_length=1, max_length=200)],
    ) -> AccessRequestLookupResult:
        return await tool_service.lookup_access_request(access_request_id)

    @server.tool(
        name="list_access_requests",
        description=(
            "List a bounded, deterministically ordered set of fictional "
            "access requests."
        ),
        annotations=READ_ONLY,
    )
    async def list_access_requests(
        approval_status: Literal["approved", "pending", "denied"] | None = None,
        limit: Annotated[int, Field(ge=1, le=100)] = 25,
    ) -> AccessRequestListResult:
        return await tool_service.list_access_requests(approval_status, limit)

    @server.tool(
        name="propose_access_request_status",
        description=(
            "Create a validated durable proposal for an access-request status "
            "change without executing the change."
        ),
        annotations=APPROVAL_PROPOSAL,
    )
    async def propose_access_request_status(
        access_request_id: Annotated[str, Field(min_length=1, max_length=200)],
        new_status: Literal["approved", "pending", "denied"],
    ) -> ApprovalProposalResult:
        return await tool_service.propose_access_request_status(
            access_request_id,
            new_status,
        )

    @server.tool(
        name="resume_access_request_approval",
        description=(
            "Explicitly approve or reject one existing durable proposal; "
            "arguments of the proposed action cannot be replaced."
        ),
        annotations=APPROVAL_RESUME,
    )
    async def resume_access_request_approval(
        approval_id: Annotated[str, Field(min_length=1, max_length=200)],
        decision: Literal["approve", "reject"],
        comment: Annotated[str | None, Field(max_length=1_000)] = None,
    ) -> ApprovalResumeResult:
        return await tool_service.resume_access_request_approval(
            approval_id,
            decision,
            comment,
        )

    return server


def create_mcp_asgi_app(
    server: MCPServer,
    settings: ServiceSettings,
) -> Starlette:
    """Create one hardened Streamable HTTP endpoint for FastAPI mounting."""

    transport_security = TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=list(settings.mcp_allowed_hosts),
        allowed_origins=list(settings.mcp_allowed_origins),
    )
    return server.streamable_http_app(
        streamable_http_path=settings.mcp_path,
        json_response=True,
        stateless_http=True,
        transport_security=transport_security,
        host=settings.api_host,
    )


__all__ = [
    "MCP_PROTOCOL_REVISION",
    "MCP_SDK_PACKAGE",
    "MCP_SDK_VERSION",
    "create_mcp_asgi_app",
    "create_mcp_server",
]
