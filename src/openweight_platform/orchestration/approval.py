"""Checkpointed human approval for future consequential actions."""

from __future__ import annotations

import json
from typing import Literal, NotRequired, Protocol, TypeAlias, TypedDict

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langchain_core.runnables import RunnableConfig
from langgraph.types import interrupt
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    ValidationError,
    field_validator,
)


ApprovalStatus: TypeAlias = Literal["pending", "approved", "rejected"]


class ActionProposal(BaseModel):
    """Human-readable, JSON-compatible description of a proposed action."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    action_type: str = Field(min_length=1)
    summary: str = Field(min_length=1)
    arguments: dict[str, JsonValue]
    consequence: str = Field(min_length=1)

    @field_validator("action_type", "summary", "consequence")
    @classmethod
    def reject_blank_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("must not be blank")
        return value

    @field_validator("arguments")
    @classmethod
    def require_finite_json_values(
        cls,
        value: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        try:
            json.dumps(value, allow_nan=False)
        except (TypeError, ValueError) as error:
            raise ValueError(
                "arguments must contain only finite JSON values"
            ) from error
        return value


class ApprovalDecision(BaseModel):
    """Strict response supplied by the human reviewer on resume."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    decision: Literal["approve", "reject"]
    comment: str | None = None

    @field_validator("comment")
    @classmethod
    def reject_blank_comment(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("comment must not be blank")
        return value


class ApprovalDecisionError(ValueError):
    """Raised when a resume value is not a valid human decision."""


class ActionExecutor(Protocol):
    def __call__(self, proposal: ActionProposal, effect_id: str) -> object: ...


class ApprovalGraphInput(TypedDict):
    action_proposal: ActionProposal
    effect_id: NotRequired[str]


class ApprovalGraphState(ApprovalGraphInput, total=False):
    approval_decision: ApprovalDecision
    approval_status: ApprovalStatus
    action_result: object


class ApprovalGraphOutput(TypedDict):
    action_proposal: ActionProposal
    approval_decision: ApprovalDecision
    approval_status: ApprovalStatus
    action_result: NotRequired[object]


def build_approval_interrupt_payload(
    proposal: ActionProposal,
) -> dict[str, object]:
    """Build the stable JSON-compatible payload presented for review."""
    return {
        "type": "approval_required",
        "action": proposal.model_dump(mode="json"),
    }


def parse_approval_decision(value: object) -> ApprovalDecision:
    """Strictly validate one value returned through ``Command(resume=...)``."""
    try:
        return ApprovalDecision.model_validate(value, strict=True)
    except ValidationError as error:
        raise ApprovalDecisionError(
            "Resume value must be an approve or reject decision object"
        ) from error


def build_human_approval_graph(
    action_executor: ActionExecutor,
    *,
    checkpointer: BaseCheckpointSaver,
) -> CompiledStateGraph:
    """Build a checkpointed interrupt/resume gate for one proposed action."""
    if checkpointer is None:
        raise ValueError("The human approval graph requires a checkpointer")
    if not callable(action_executor):
        raise TypeError("action_executor must be callable")

    def prepare_action(
        state: ApprovalGraphState,
        config: RunnableConfig,
    ) -> dict[str, object]:
        proposal = _validated_proposal(state.get("action_proposal"))
        effect_id = state.get("effect_id") or config.get("configurable", {}).get(
            "thread_id"
        )
        return {
            "action_proposal": proposal,
            "effect_id": _validated_effect_id(effect_id),
            "approval_status": "pending",
        }

    def request_approval(state: ApprovalGraphState) -> dict[str, object]:
        proposal = _validated_proposal(state.get("action_proposal"))
        resume_value = interrupt(build_approval_interrupt_payload(proposal))
        decision = parse_approval_decision(resume_value)
        status: ApprovalStatus = (
            "approved" if decision.decision == "approve" else "rejected"
        )
        return {
            "approval_decision": decision,
            "approval_status": status,
        }

    def approval_route(state: ApprovalGraphState) -> Literal[
        "approved", "rejected"
    ]:
        decision, status = _validated_reviewed_state(state)
        if decision.decision == "approve" and status == "approved":
            return "approved"
        if decision.decision == "reject" and status == "rejected":
            return "rejected"
        raise ValueError("Approval decision and status are inconsistent")

    def execute_action(state: ApprovalGraphState) -> dict[str, object]:
        proposal = _validated_proposal(state.get("action_proposal"))
        decision, status = _validated_reviewed_state(state)
        if decision.decision != "approve" or status != "approved":
            raise ValueError("Action execution requires explicit approval")
        effect_id = _validated_effect_id(state.get("effect_id"))
        return {"action_result": action_executor(proposal, effect_id)}

    graph = StateGraph(
        ApprovalGraphState,
        input_schema=ApprovalGraphInput,
        output_schema=ApprovalGraphOutput,
    )
    graph.add_node("prepare_action", prepare_action)
    graph.add_node("request_approval", request_approval)
    graph.add_node("execute_action", execute_action)

    graph.add_edge(START, "prepare_action")
    graph.add_edge("prepare_action", "request_approval")
    graph.add_conditional_edges(
        "request_approval",
        approval_route,
        {
            "approved": "execute_action",
            "rejected": END,
        },
    )
    graph.add_edge("execute_action", END)

    return graph.compile(
        checkpointer=checkpointer,
        name="human_approval_graph",
    )


def _validated_proposal(value: object) -> ActionProposal:
    if not isinstance(value, ActionProposal):
        raise TypeError("Graph state requires a validated ActionProposal")
    return value


def _validated_effect_id(value: object) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > 128:
        raise ValueError("Graph state requires a bounded non-empty effect_id")
    return value


def _validated_reviewed_state(
    state: ApprovalGraphState,
) -> tuple[ApprovalDecision, ApprovalStatus]:
    decision = state.get("approval_decision")
    if not isinstance(decision, ApprovalDecision):
        raise TypeError("Graph state requires a validated ApprovalDecision")

    status = state.get("approval_status")
    if status not in ("approved", "rejected"):
        raise ValueError("Graph state requires a completed approval status")
    return decision, status


__all__ = [
    "ActionExecutor",
    "ActionProposal",
    "ApprovalDecision",
    "ApprovalDecisionError",
    "ApprovalGraphInput",
    "ApprovalGraphOutput",
    "ApprovalGraphState",
    "ApprovalStatus",
    "build_approval_interrupt_payload",
    "build_human_approval_graph",
    "parse_approval_decision",
]
