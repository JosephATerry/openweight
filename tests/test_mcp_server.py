from __future__ import annotations

import asyncio
import subprocess
import sys
from typing import Any

from mcp.client import Client

from openweight_platform.api.config import ServiceSettings
from openweight_platform.api.errors import StateConflictError
from openweight_platform.api.observability import create_observability
from openweight_platform.api.runtime import ApprovalOutcome, PendingApproval
from openweight_platform.api.security import AuthorizationBoundary
from openweight_platform.mcp.server import (
    MCP_PROTOCOL_REVISION,
    MCP_SDK_PACKAGE,
    MCP_SDK_VERSION,
    create_mcp_server,
)
from openweight_platform.orchestration.approval import (
    ActionProposal,
    ApprovalDecision,
)


EXPECTED_TOOLS = [
    "search_policy",
    "lookup_employee",
    "lookup_contractor",
    "lookup_access_request",
    "list_access_requests",
    "propose_access_request_status",
    "resume_access_request_approval",
]


def settings(**overrides: str) -> ServiceSettings:
    return ServiceSettings.from_env(
        {
            "OPENWEIGHT_DATABASE_REQUIRED": "false",
            "OPENWEIGHT_ENVIRONMENT": "test",
            "OPENWEIGHT_MCP_ENABLED": "true",
            **overrides,
        }
    )


class FakeMcpRuntime:
    backend_alias = "fake"

    def __init__(self) -> None:
        self.write_count = 0
        self.proposal_count = 0
        self._completed: set[str] = set()
        self._resume_lock = asyncio.Lock()
        self.request_ids: list[str] = []

    async def search_policy(
        self,
        query: str,
        *,
        limit: int,
        request_id: str,
    ) -> list[dict[str, object]]:
        self.request_ids.append(request_id)
        return [
            {
                "content": f"Fictional policy match for {query}.",
                "metadata": {"source": "policy://fictional/access"},
            }
        ][:limit]

    async def lookup_employee(
        self,
        identifier: str,
        *,
        request_id: str,
    ) -> dict[str, object]:
        self.request_ids.append(request_id)
        return {
            "employee_id": identifier,
            "full_name": "Fictional Employee",
            "employment_status": "active",
            "department": "Security",
            "manager_employee_id": None,
            "mfa_enrolled": True,
            "security_training_current": True,
        }

    async def lookup_contractor(
        self,
        identifier: str,
        *,
        request_id: str,
    ) -> dict[str, object]:
        self.request_ids.append(request_id)
        return {
            "contractor_id": identifier,
            "full_name": "Fictional Contractor",
            "vendor_name": "Example Vendor",
            "engagement_status": "active",
            "sponsor_employee_id": "fic-emp-001",
            "engagement_end_date": "2027-01-01",
            "managed_identity": True,
            "mfa_enrolled": True,
        }

    async def lookup_access_request(
        self,
        access_request_id: str,
        *,
        request_id: str,
    ) -> dict[str, object]:
        self.request_ids.append(request_id)
        return self._access_request(access_request_id)

    async def list_access_requests(
        self,
        *,
        approval_status: str | None,
        limit: int,
        request_id: str,
    ) -> list[dict[str, object]]:
        self.request_ids.append(request_id)
        records = [self._access_request("fic-req-001")]
        if approval_status and records[0]["approval_status"] != approval_status:
            return []
        return records[:limit]

    @staticmethod
    def _access_request(access_request_id: str) -> dict[str, object]:
        return {
            "request_id": access_request_id,
            "subject_type": "employee",
            "subject_id": "fic-emp-001",
            "system_name": "Fictional Finance",
            "requested_role": "reader",
            "approval_status": "pending",
            "requested_start_date": "2026-01-01",
            "requested_end_date": None,
        }

    async def propose_access_request_status(
        self,
        access_request_id: str,
        new_status: str,
        *,
        request_id: str,
    ) -> PendingApproval:
        self.request_ids.append(request_id)
        self.proposal_count += 1
        return PendingApproval(
            approval_id=f"approval-{self.proposal_count}",
            proposal=ActionProposal(
                action_type="set_access_request_status",
                summary="A fixed status change is pending explicit approval.",
                arguments={
                    "request_id": access_request_id,
                    "new_status": new_status,
                },
                consequence="The fictional request status may change.",
            ),
        )

    async def resume_approval(
        self,
        approval_id: str,
        decision: ApprovalDecision,
        *,
        request_id: str,
    ) -> ApprovalOutcome:
        self.request_ids.append(request_id)
        async with self._resume_lock:
            if approval_id in self._completed:
                raise StateConflictError
            self._completed.add(approval_id)
            if decision.decision == "approve":
                self.write_count += 1
                return ApprovalOutcome(
                    approval_id=approval_id,
                    status="approved",
                    action_result={"request_id": "fic-req-001", "changed": True},
                )
            return ApprovalOutcome(
                approval_id=approval_id,
                status="rejected",
                action_result=None,
            )

    async def close(self) -> None:
        return None


def server(runtime: FakeMcpRuntime):
    service_settings = settings()
    observability = create_observability(service_settings)
    return (
        create_mcp_server(
            settings=service_settings,
            runtime_provider=lambda: runtime,
            observability=observability,
            authorization_boundary=AuthorizationBoundary(service_settings),
        ),
        observability,
    )


def run(coroutine):
    return asyncio.run(coroutine)


def test_official_sdk_revision_and_deterministic_discovery() -> None:
    runtime = FakeMcpRuntime()
    mcp_server, _ = server(runtime)

    async def exercise() -> tuple[str, list[Any], list[Any]]:
        async with Client(mcp_server) as client:
            first = (await client.list_tools()).tools
            second = (await client.list_tools(cache_mode="refresh")).tools
            return client.protocol_version, first, second

    revision, first, second = run(exercise())

    assert MCP_PROTOCOL_REVISION == "2026-07-28"
    assert MCP_SDK_PACKAGE == "mcp"
    assert MCP_SDK_VERSION == "2.1.1"
    assert revision == MCP_PROTOCOL_REVISION
    assert [tool.name for tool in first] == EXPECTED_TOOLS
    assert [tool.model_dump() for tool in first] == [
        tool.model_dump() for tool in second
    ]
    assert first[0].annotations.read_only_hint is True
    assert first[-2].annotations.read_only_hint is False
    assert first[-2].annotations.destructive_hint is False
    assert first[-1].annotations.destructive_hint is True


def test_server_registers_no_resources_or_prompts() -> None:
    mcp_server, _ = server(FakeMcpRuntime())

    async def exercise() -> tuple[list[Any], list[Any]]:
        async with Client(mcp_server) as client:
            return (
                (await client.list_resources()).resources,
                (await client.list_prompts()).prompts,
            )

    resources, prompts = run(exercise())
    assert resources == []
    assert prompts == []


def test_same_server_negotiates_the_supported_legacy_revision() -> None:
    mcp_server, _ = server(FakeMcpRuntime())

    async def exercise() -> tuple[str, list[str]]:
        async with Client(mcp_server, mode="legacy") as client:
            return client.protocol_version, [
                tool.name for tool in (await client.list_tools()).tools
            ]

    revision, tools = run(exercise())
    assert revision == "2025-11-25"
    assert tools == EXPECTED_TOOLS


def test_read_only_catalog_returns_structured_results() -> None:
    mcp_server, _ = server(FakeMcpRuntime())

    async def exercise() -> list[dict[str, Any]]:
        async with Client(mcp_server) as client:
            calls = [
                ("search_policy", {"query": "MFA", "limit": 1}),
                ("lookup_employee", {"identifier": "fic-emp-001"}),
                ("lookup_contractor", {"identifier": "fic-con-001"}),
                ("lookup_access_request", {"access_request_id": "fic-req-001"}),
                ("list_access_requests", {"approval_status": "pending", "limit": 5}),
            ]
            return [
                (await client.call_tool(name, arguments)).structured_content
                for name, arguments in calls
            ]

    results = run(exercise())
    assert results[0]["matches"][0]["content"].startswith("Fictional policy")
    assert results[1]["employee"]["employee_id"] == "fic-emp-001"
    assert results[2]["contractor"]["contractor_id"] == "fic-con-001"
    assert results[3]["access_request"]["request_id"] == "fic-req-001"
    assert len(results[4]["access_requests"]) == 1


def test_malformed_and_unknown_tool_fail_safely() -> None:
    mcp_server, _ = server(FakeMcpRuntime())

    async def exercise():
        async with Client(mcp_server) as client:
            malformed = await client.call_tool(
                "lookup_employee",
                {"identifier": ""},
            )
            unknown = await client.call_tool("execute_sql", {"sql": "SELECT 1"})
            return malformed, unknown

    malformed, unknown = run(exercise())
    assert malformed.is_error is True
    assert unknown.is_error is True
    rendered = " ".join(item.text for item in malformed.content if hasattr(item, "text"))
    assert "Traceback" not in rendered
    assert "password" not in rendered.lower()


def test_proposal_does_not_write_and_resume_writes_once() -> None:
    runtime = FakeMcpRuntime()
    mcp_server, _ = server(runtime)

    async def exercise():
        async with Client(mcp_server) as client:
            proposal = await client.call_tool(
                "propose_access_request_status",
                {"access_request_id": "fic-req-001", "new_status": "approved"},
            )
            assert runtime.write_count == 0
            approval_id = proposal.structured_content["approval_id"]
            approved = await client.call_tool(
                "resume_access_request_approval",
                {"approval_id": approval_id, "decision": "approve"},
            )
            replay = await client.call_tool(
                "resume_access_request_approval",
                {"approval_id": approval_id, "decision": "approve"},
            )
            return approved, replay

    approved, replay = run(exercise())
    assert approved.structured_content["status"] == "approved"
    assert replay.is_error is True
    assert runtime.write_count == 1


def test_concurrent_resume_has_exactly_one_effect() -> None:
    runtime = FakeMcpRuntime()
    mcp_server, _ = server(runtime)

    async def exercise():
        async with Client(mcp_server) as client:
            proposal = await client.call_tool(
                "propose_access_request_status",
                {"access_request_id": "fic-req-001", "new_status": "denied"},
            )
            approval_id = proposal.structured_content["approval_id"]
            return await asyncio.gather(
                client.call_tool(
                    "resume_access_request_approval",
                    {"approval_id": approval_id, "decision": "approve"},
                ),
                client.call_tool(
                    "resume_access_request_approval",
                    {"approval_id": approval_id, "decision": "approve"},
                ),
            )

    outcomes = run(exercise())
    assert sum(not outcome.is_error for outcome in outcomes) == 1
    assert sum(outcome.is_error for outcome in outcomes) == 1
    assert runtime.write_count == 1


def test_rejection_has_no_effect() -> None:
    runtime = FakeMcpRuntime()
    mcp_server, _ = server(runtime)

    async def exercise():
        async with Client(mcp_server) as client:
            proposal = await client.call_tool(
                "propose_access_request_status",
                {"access_request_id": "fic-req-001", "new_status": "approved"},
            )
            return await client.call_tool(
                "resume_access_request_approval",
                {
                    "approval_id": proposal.structured_content["approval_id"],
                    "decision": "reject",
                },
            )

    rejected = run(exercise())
    assert rejected.structured_content["status"] == "rejected"
    assert runtime.write_count == 0


def test_mcp_metrics_are_bounded_and_payload_free() -> None:
    runtime = FakeMcpRuntime()
    mcp_server, observability = server(runtime)

    async def exercise() -> None:
        async with Client(mcp_server) as client:
            await client.call_tool(
                "lookup_employee",
                {"identifier": "private-payload-value"},
            )

    run(exercise())
    payload = observability.metrics_payload().decode()
    assert 'tool_name="lookup_employee"' in payload
    assert "private-payload-value" not in payload
    assert runtime.request_ids[0] not in payload


def test_importing_mcp_layer_does_not_import_model_frameworks() -> None:
    command = (
        "import sys; import openweight_platform.mcp.server; "
        "assert 'torch' not in sys.modules; "
        "assert 'transformers' not in sys.modules"
    )
    completed = subprocess.run(
        [sys.executable, "-c", command],
        env={"PYTHONPATH": "src"},
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr
