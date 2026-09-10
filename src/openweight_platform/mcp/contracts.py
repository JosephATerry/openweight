"""Typed, reasoning-free MCP tool results."""

from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue

from openweight_platform.orchestration.approval import ActionProposal


class McpModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class EmployeeRecord(McpModel):
    employee_id: str
    full_name: str
    employment_status: str
    department: str
    manager_employee_id: str | None
    mfa_enrolled: bool
    security_training_current: bool


class ContractorRecord(McpModel):
    contractor_id: str
    full_name: str
    vendor_name: str
    engagement_status: str
    sponsor_employee_id: str
    engagement_end_date: date
    managed_identity: bool
    mfa_enrolled: bool


class AccessRequestRecord(McpModel):
    request_id: str
    subject_type: str
    subject_id: str
    system_name: str
    requested_role: str
    approval_status: str
    requested_start_date: date
    requested_end_date: date | None


class PolicyMatch(McpModel):
    content: str
    metadata: dict[str, JsonValue] = Field(default_factory=dict)


class PolicySearchResult(McpModel):
    request_id: str
    matches: list[PolicyMatch]


class EmployeeLookupResult(McpModel):
    request_id: str
    employee: EmployeeRecord


class ContractorLookupResult(McpModel):
    request_id: str
    contractor: ContractorRecord


class AccessRequestLookupResult(McpModel):
    request_id: str
    access_request: AccessRequestRecord


class AccessRequestListResult(McpModel):
    request_id: str
    access_requests: list[AccessRequestRecord]


class ApprovalProposalResult(McpModel):
    request_id: str
    status: Literal["approval_required"] = "approval_required"
    approval_required: Literal[True] = True
    approval_id: str
    proposal: ActionProposal


class ApprovalResumeResult(McpModel):
    request_id: str
    approval_id: str
    status: Literal["approved", "rejected"]
    action_result: JsonValue | None = None


__all__ = [
    "AccessRequestListResult",
    "AccessRequestLookupResult",
    "AccessRequestRecord",
    "ApprovalProposalResult",
    "ApprovalResumeResult",
    "ContractorLookupResult",
    "ContractorRecord",
    "EmployeeLookupResult",
    "EmployeeRecord",
    "McpModel",
    "PolicyMatch",
    "PolicySearchResult",
]
