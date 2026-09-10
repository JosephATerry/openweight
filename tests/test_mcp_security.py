from __future__ import annotations

import asyncio
from dataclasses import dataclass

import httpx
import pytest
from fastapi import FastAPI

from openweight_platform.api.app import create_app
from openweight_platform.api.config import ServiceSettings
from openweight_platform.api.errors import AuthenticationRequiredError
from openweight_platform.api.runtime import ApprovalOutcome, PendingApproval
from openweight_platform.api.security import (
    AuthenticatedPrincipal,
    ROLE_PERMISSIONS,
)
from openweight_platform.orchestration.approval import (
    ActionProposal,
    ApprovalDecision,
)


class StaticVerifier:
    def verify(self, token: str) -> AuthenticatedPrincipal:
        role = {
            "reader-token": "OpenWeight.Reader",
            "approver-token": "OpenWeight.Approver",
            "operator-token": "OpenWeight.Operator",
        }.get(token)
        if role is None:
            raise AuthenticationRequiredError
        return AuthenticatedPrincipal(
            subject=f"{role}-subject",
            permissions=ROLE_PERMISSIONS[role],
        )


@dataclass
class AuthRuntime:
    backend_alias: str = "fake"
    proposal_calls: int = 0
    resume_calls: int = 0

    async def lookup_employee(self, identifier: str, *, request_id: str):
        return {
            "employee_id": identifier,
            "full_name": "Fictional Employee",
            "employment_status": "active",
            "department": "Security",
            "manager_employee_id": None,
            "mfa_enrolled": True,
            "security_training_current": True,
        }

    async def propose_access_request_status(
        self,
        access_request_id: str,
        new_status: str,
        *,
        request_id: str,
    ) -> PendingApproval:
        self.proposal_calls += 1
        return PendingApproval(
            approval_id="approval-auth",
            proposal=ActionProposal(
                action_type="set_access_request_status",
                summary="A fixed action awaits approval.",
                arguments={
                    "request_id": access_request_id,
                    "new_status": new_status,
                },
                consequence="A fictional status may change.",
            ),
        )

    async def resume_approval(
        self,
        approval_id: str,
        decision: ApprovalDecision,
        *,
        request_id: str,
    ) -> ApprovalOutcome:
        self.resume_calls += 1
        return ApprovalOutcome(
            approval_id=approval_id,
            status="approved" if decision.decision == "approve" else "rejected",
            action_result=None,
        )

    async def close(self) -> None:
        return None


def auth_settings() -> ServiceSettings:
    return ServiceSettings.from_env(
        {
            "OPENWEIGHT_DATABASE_REQUIRED": "false",
            "OPENWEIGHT_ENVIRONMENT": "test",
            "OPENWEIGHT_MCP_ENABLED": "true",
            "OPENWEIGHT_MCP_ALLOWED_HOSTS": "testserver",
            "OPENWEIGHT_AUTH_ENABLED": "true",
            "OPENWEIGHT_AUTH_ISSUER": "https://issuer.example.invalid/tenant/v2.0",
            "OPENWEIGHT_AUTH_AUDIENCE": "api://openweight-platform",
            "OPENWEIGHT_AUTH_JWKS_URL": "https://issuer.example.invalid/keys",
            "OPENWEIGHT_MCP_RESOURCE_SERVER_URL": "http://testserver/mcp",
            "OPENWEIGHT_METRICS_ACCESS_MODE": "protected",
        }
    )


def app(runtime: AuthRuntime | None = None) -> tuple[FastAPI, AuthRuntime]:
    selected_runtime = runtime or AuthRuntime()
    return (
        create_app(
            settings=auth_settings(),
            runtime=selected_runtime,
            token_verifier=StaticVerifier(),
        ),
        selected_runtime,
    )


MCP_META = {
    "io.modelcontextprotocol/protocolVersion": "2026-07-28",
    "io.modelcontextprotocol/clientCapabilities": {},
    "io.modelcontextprotocol/clientInfo": {
        "name": "openweight-test-client",
        "version": "1.0",
    },
}


def mcp_request(
    application: FastAPI,
    method: str,
    params: dict[str, object],
    *,
    token: str | None,
    request_id: str = "mcp-security-request",
) -> httpx.Response:
    async def perform() -> httpx.Response:
        headers = {
            "Content-Type": "application/json",
            "MCP-Protocol-Version": "2026-07-28",
            "MCP-Method": method,
            "X-Request-ID": request_id,
        }
        if method == "tools/call":
            headers["MCP-Name"] = str(params["name"])
        if token is not None:
            headers["Authorization"] = f"Bearer {token}"
        body_params = {**params, "_meta": MCP_META}
        async with application.router.lifespan_context(application):
            transport = httpx.ASGITransport(
                app=application,
                raise_app_exceptions=False,
            )
            async with httpx.AsyncClient(
                transport=transport,
                base_url="http://testserver",
            ) as client:
                return await client.post(
                    "/mcp",
                    headers=headers,
                    json={
                        "jsonrpc": "2.0",
                        "id": 1,
                        "method": method,
                        "params": body_params,
                    },
                )

    return asyncio.run(perform())


def tool_call(
    application: FastAPI,
    token: str | None,
    name: str,
    arguments: dict[str, object],
) -> httpx.Response:
    return mcp_request(
        application,
        "tools/call",
        {"name": name, "arguments": arguments},
        token=token,
    )


def tool_calls(
    application: FastAPI,
    calls: list[tuple[str | None, str, dict[str, object]]],
) -> list[httpx.Response]:
    async def perform() -> list[httpx.Response]:
        responses: list[httpx.Response] = []
        async with application.router.lifespan_context(application):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(
                    app=application,
                    raise_app_exceptions=False,
                ),
                base_url="http://testserver",
            ) as client:
                for token, name, arguments in calls:
                    headers = {
                        "Content-Type": "application/json",
                        "MCP-Protocol-Version": "2026-07-28",
                        "MCP-Method": "tools/call",
                        "MCP-Name": name,
                        "X-Request-ID": "mcp-security-request",
                    }
                    if token is not None:
                        headers["Authorization"] = f"Bearer {token}"
                    responses.append(
                        await client.post(
                            "/mcp",
                            headers=headers,
                            json={
                                "jsonrpc": "2.0",
                                "id": len(responses) + 1,
                                "method": "tools/call",
                                "params": {
                                    "name": name,
                                    "arguments": arguments,
                                    "_meta": MCP_META,
                                },
                            },
                        )
                    )
        return responses

    return asyncio.run(perform())


def test_missing_and_invalid_bearer_fail_before_protocol_dispatch(capsys) -> None:
    missing = mcp_request(app()[0], "server/discover", {}, token=None)
    invalid_value = "sensitive-invalid-bearer-value"
    invalid = mcp_request(
        app()[0],
        "server/discover",
        {},
        token=invalid_value,
    )

    assert missing.status_code == 401
    assert invalid.status_code == 401
    assert "resource_metadata=" in missing.headers["WWW-Authenticate"]
    combined = missing.text + invalid.text + capsys.readouterr().err
    assert invalid_value not in combined
    assert "Traceback" not in invalid.text


def test_reader_can_read_but_cannot_propose_or_resume() -> None:
    application, runtime = app()
    read, proposal, resume = tool_calls(
        application,
        [
            ("reader-token", "lookup_employee", {"identifier": "fic-emp-001"}),
            (
                "reader-token",
                "propose_access_request_status",
                {"access_request_id": "fic-req-001", "new_status": "approved"},
            ),
            (
                "reader-token",
                "resume_access_request_approval",
                {"approval_id": "approval-auth", "decision": "approve"},
            ),
        ],
    )

    assert read.status_code == 200
    assert read.json()["result"]["isError"] is False
    assert proposal.json()["result"]["isError"] is True
    assert "authorization_error" in proposal.text
    assert resume.json()["result"]["isError"] is True
    assert runtime.proposal_calls == 0
    assert runtime.resume_calls == 0


def test_approver_can_read_and_resume_but_cannot_propose() -> None:
    application, runtime = app()
    read, proposal, resume = tool_calls(
        application,
        [
            ("approver-token", "lookup_employee", {"identifier": "fic-emp-001"}),
            (
                "approver-token",
                "propose_access_request_status",
                {"access_request_id": "fic-req-001", "new_status": "approved"},
            ),
            (
                "approver-token",
                "resume_access_request_approval",
                {"approval_id": "approval-auth", "decision": "approve"},
            ),
        ],
    )

    assert read.json()["result"]["isError"] is False
    assert proposal.json()["result"]["isError"] is True
    assert resume.json()["result"]["isError"] is False
    assert runtime.proposal_calls == 0
    assert runtime.resume_calls == 1


def test_operator_can_propose_and_resume() -> None:
    application, runtime = app()
    proposal, resume = tool_calls(
        application,
        [
            (
                "operator-token",
                "propose_access_request_status",
                {"access_request_id": "fic-req-001", "new_status": "approved"},
            ),
            (
                "operator-token",
                "resume_access_request_approval",
                {"approval_id": "approval-auth", "decision": "approve"},
            ),
        ],
    )

    assert proposal.json()["result"]["isError"] is False
    assert resume.json()["result"]["isError"] is False
    assert runtime.proposal_calls == 1
    assert runtime.resume_calls == 1


def test_request_id_is_correlated_but_not_used_as_identity() -> None:
    application, _ = app()
    response = tool_call(
        application,
        "reader-token",
        "lookup_employee",
        {"identifier": "fic-emp-001"},
    )

    assert response.headers["X-Request-ID"] == "mcp-security-request"
    structured = response.json()["result"]["structuredContent"]
    assert structured["request_id"] == "mcp-security-request"


def test_protected_resource_metadata_is_origin_discoverable() -> None:
    application, _ = app()

    async def perform() -> httpx.Response:
        async with application.router.lifespan_context(application):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=application),
                base_url="http://testserver",
            ) as client:
                return await client.get(
                    "/.well-known/oauth-protected-resource/mcp"
                )

    response = asyncio.run(perform())
    assert response.status_code == 200
    assert response.json()["resource"] == "http://testserver/mcp"
    assert response.json()["authorization_servers"] == [
        "https://issuer.example.invalid/tenant/v2.0"
    ]


@pytest.mark.parametrize(
    ("environment", "resource_url", "expected_error"),
    [
        ("production", "http://example.invalid/mcp", True),
        ("production", "https://example.invalid/mcp", False),
        ("test", "http://testserver/mcp", False),
    ],
)
def test_mcp_resource_server_url_requires_https_outside_local_modes(
    environment: str,
    resource_url: str,
    expected_error: bool,
) -> None:
    values = {
        "OPENWEIGHT_DATABASE_REQUIRED": "false",
        "OPENWEIGHT_ENVIRONMENT": environment,
        "OPENWEIGHT_MCP_ENABLED": "true",
        "OPENWEIGHT_AUTH_ENABLED": "true",
        "OPENWEIGHT_AUTH_ISSUER": "https://issuer.example.invalid",
        "OPENWEIGHT_AUTH_AUDIENCE": "api://openweight-platform",
        "OPENWEIGHT_AUTH_JWKS_URL": "https://issuer.example.invalid/keys",
        "OPENWEIGHT_MCP_RESOURCE_SERVER_URL": resource_url,
        "OPENWEIGHT_METRICS_ACCESS_MODE": "disabled",
    }
    result = ServiceSettings.from_env(values)
    has_mcp_error = any("MCP resource-server URL" in item for item in result.configuration_errors)
    assert has_mcp_error is expected_error
