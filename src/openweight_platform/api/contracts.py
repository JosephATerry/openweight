"""Public, reasoning-free HTTP request and response contracts."""

from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    field_validator,
)

from openweight_platform.orchestration.approval import ActionProposal


class ApiModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class AgentQueryRequest(ApiModel):
    question: str = Field(min_length=1, max_length=4_000)

    @field_validator("question")
    @classmethod
    def reject_blank_question(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("question must not be blank")
        return value


class AgentQueryResponse(ApiModel):
    request_id: str
    status: Literal["completed"] = "completed"
    route: Literal["policy", "operations", "web"]
    routing_decision: dict[str, JsonValue]
    result: JsonValue


class PolicyQueryRequest(AgentQueryRequest):
    pass


class PolicyEvidenceResponse(ApiModel):
    citation_id: str
    policy_id: str
    title: str
    domain: str
    chunk_index: int = Field(ge=0)
    content: str


class PolicyQueryResponse(ApiModel):
    request_id: str
    status: Literal["answered", "insufficient_evidence"]
    source_scope: Literal["internal_policy"] = "internal_policy"
    answer: str
    citations: list[str]
    citation_valid: bool
    evidence: list[PolicyEvidenceResponse]


class AccessRequestStatusProposalRequest(ApiModel):
    new_status: Literal["approved", "pending", "denied"]


class ApprovalProposalResponse(ApiModel):
    request_id: str
    status: Literal["approval_required"] = "approval_required"
    approval_required: Literal[True] = True
    approval_id: str
    proposal: ActionProposal


class ApprovalResumeRequest(ApiModel):
    decision: Literal["approve", "reject"]
    comment: str | None = Field(default=None, max_length=1_000)

    @field_validator("comment")
    @classmethod
    def reject_blank_comment(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("comment must not be blank")
        return value


class ApprovalResumeResponse(ApiModel):
    request_id: str
    approval_id: str
    status: Literal["approved", "rejected"]
    action_result: JsonValue | None = None


class AccessRequestRecord(ApiModel):
    request_id: str
    subject_type: Literal["employee", "contractor"]
    subject_id: str
    system_name: str
    requested_role: str
    approval_status: Literal["approved", "pending", "denied"]
    requested_start_date: date
    requested_end_date: date | None = None


class AccessRequestListResponse(ApiModel):
    request_id: str
    access_requests: list[AccessRequestRecord]


class AccessRequestDetailResponse(ApiModel):
    request_id: str
    access_request: AccessRequestRecord


class HealthResponse(ApiModel):
    request_id: str
    status: Literal["alive"] = "alive"
    service: str


class DependencyStatus(ApiModel):
    name: str
    status: Literal["ready", "unavailable", "disabled"]
    required: bool
    detail: str


class ReadinessResponse(ApiModel):
    request_id: str
    status: Literal["ready", "not_ready"]
    dependencies: list[DependencyStatus]


class ServiceInfoResponse(ApiModel):
    request_id: str
    service: str
    version: str
    environment: str
    deployment_profile: Literal["local", "huggingface"]
    backend: str
    model_id: str
    inference_provider: str
    inference_configured: bool
    inference_state: Literal[
        "not_initialized",
        "loaded",
        "unconfigured",
        "not_used",
        "requesting",
        "available",
        "unavailable",
    ]
    demo_state: Literal["durable", "ephemeral"]
    build_sha: str | None = None


class ApiErrorDetail(ApiModel):
    code: str
    message: str


class ApiErrorResponse(ApiModel):
    request_id: str
    status: Literal["error"] = "error"
    error: ApiErrorDetail


__all__ = [
    "AccessRequestDetailResponse",
    "AccessRequestListResponse",
    "AccessRequestRecord",
    "AccessRequestStatusProposalRequest",
    "AgentQueryRequest",
    "AgentQueryResponse",
    "ApiErrorDetail",
    "ApiErrorResponse",
    "ApprovalProposalResponse",
    "ApprovalResumeRequest",
    "ApprovalResumeResponse",
    "DependencyStatus",
    "HealthResponse",
    "PolicyEvidenceResponse",
    "PolicyQueryRequest",
    "PolicyQueryResponse",
    "ReadinessResponse",
    "ServiceInfoResponse",
]
