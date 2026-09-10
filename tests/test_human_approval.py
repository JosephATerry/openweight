from __future__ import annotations

import json

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command, Interrupt
from pydantic import ValidationError

from openweight_platform.orchestration.approval import (
    ActionProposal,
    ApprovalDecision,
    ApprovalDecisionError,
    build_approval_interrupt_payload,
    build_human_approval_graph,
    parse_approval_decision,
)


class FakeExecutor:
    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.proposals: list[ActionProposal] = []

    def __call__(self, proposal: ActionProposal, effect_id: str) -> object:
        assert effect_id
        self.proposals.append(proposal)
        if self.error is not None:
            raise self.error
        return {
            "executed": True,
            "action_type": proposal.action_type,
        }


def example_proposal() -> ActionProposal:
    return ActionProposal(
        action_type="disable_fictional_account",
        summary="Disable the fictional employee account fic-emp-001.",
        arguments={
            "employee_id": "fic-emp-001",
            "notify_sponsor": True,
        },
        consequence="The fictional account would lose system access.",
    )


def thread_config(thread_id: str) -> dict[str, dict[str, str]]:
    return {"configurable": {"thread_id": thread_id}}


def pause_graph(graph, proposal, config):
    return graph.invoke({"action_proposal": proposal}, config=config)


def test_action_proposal_is_typed_frozen_and_json_compatible() -> None:
    proposal = example_proposal()
    serialized = proposal.model_dump(mode="json")

    assert serialized == {
        "action_type": "disable_fictional_account",
        "summary": "Disable the fictional employee account fic-emp-001.",
        "arguments": {
            "employee_id": "fic-emp-001",
            "notify_sponsor": True,
        },
        "consequence": "The fictional account would lose system access.",
    }
    assert json.loads(json.dumps(serialized, allow_nan=False)) == serialized

    with pytest.raises(ValidationError):
        proposal.summary = "Changed after review"  # type: ignore[misc]
    with pytest.raises(ValidationError):
        ActionProposal(
            action_type="bad_argument",
            summary="Contains a non-JSON value.",
            arguments={"callback": lambda: None},
            consequence="Not executable.",
        )
    with pytest.raises(ValidationError, match="finite JSON values"):
        ActionProposal(
            action_type="bad_number",
            summary="Contains an infinite number.",
            arguments={"amount": float("inf")},
            consequence="Not executable.",
        )


def test_interrupt_payload_is_stable_and_json_compatible() -> None:
    proposal = example_proposal()

    payload = build_approval_interrupt_payload(proposal)

    assert payload == {
        "type": "approval_required",
        "action": proposal.model_dump(mode="json"),
    }
    assert json.loads(json.dumps(payload, allow_nan=False)) == payload


@pytest.mark.parametrize(
    "invalid_value",
    [
        "approve",
        {"decision": "yes"},
        {"decision": 1},
        {"decision": "approve", "comment": 1},
        {"decision": "approve", "unexpected": True},
        {"decision": "approve", "comment": "  "},
    ],
)
def test_approval_decision_validation_is_strict(invalid_value) -> None:
    with pytest.raises(ApprovalDecisionError):
        parse_approval_decision(invalid_value)


def test_graph_reaches_real_interrupt_and_persists_pending_state() -> None:
    proposal = example_proposal()
    executor = FakeExecutor()
    graph = build_human_approval_graph(
        executor,
        checkpointer=InMemorySaver(),
    )
    config = thread_config("approval-pending")

    paused = pause_graph(graph, proposal, config)
    snapshot = graph.get_state(config)

    assert executor.proposals == []
    assert paused["action_proposal"] == proposal
    assert paused["approval_status"] == "pending"
    assert len(paused["__interrupt__"]) == 1
    returned_interrupt = paused["__interrupt__"][0]
    assert isinstance(returned_interrupt, Interrupt)
    assert returned_interrupt.value == {
        "type": "approval_required",
        "action": proposal.model_dump(mode="json"),
    }
    assert snapshot.values == {
        "action_proposal": proposal,
        "effect_id": "approval-pending",
        "approval_status": "pending",
    }
    assert snapshot.next == ("request_approval",)
    assert snapshot.interrupts == (returned_interrupt,)


def test_same_thread_approve_executes_once_and_persists_result() -> None:
    proposal = example_proposal()
    executor = FakeExecutor()
    graph = build_human_approval_graph(
        executor,
        checkpointer=InMemorySaver(),
    )
    config = thread_config("approval-approved")
    pause_graph(graph, proposal, config)

    result = graph.invoke(
        Command(
            resume={
                "decision": "approve",
                "comment": "Approved for the fictional test.",
            }
        ),
        config=config,
    )
    snapshot = graph.get_state(config)

    assert executor.proposals == [proposal]
    assert result["action_proposal"] == proposal
    assert result["approval_decision"] == ApprovalDecision(
        decision="approve",
        comment="Approved for the fictional test.",
    )
    assert result["approval_status"] == "approved"
    assert result["action_result"] == {
        "executed": True,
        "action_type": "disable_fictional_account",
    }
    assert snapshot.values == {**result, "effect_id": "approval-approved"}
    assert snapshot.next == ()

    repeated_result = graph.invoke(
        Command(resume={"decision": "approve"}),
        config=config,
    )
    assert repeated_result == result
    assert executor.proposals == [proposal]


def test_same_thread_reject_never_executes_and_persists_rejection() -> None:
    proposal = example_proposal()
    executor = FakeExecutor()
    graph = build_human_approval_graph(
        executor,
        checkpointer=InMemorySaver(),
    )
    config = thread_config("approval-rejected")
    pause_graph(graph, proposal, config)

    result = graph.invoke(
        Command(
            resume={
                "decision": "reject",
                "comment": "The request lacks justification.",
            }
        ),
        config=config,
    )

    assert executor.proposals == []
    assert result["approval_decision"] == ApprovalDecision(
        decision="reject",
        comment="The request lacks justification.",
    )
    assert result["approval_status"] == "rejected"
    assert "action_result" not in result
    assert graph.get_state(config).values == {
        **result,
        "effect_id": "approval-rejected",
    }
    assert graph.get_state(config).next == ()


@pytest.mark.parametrize(
    "resume_value",
    [
        {"decision": "yes"},
        {"decision": "approve", "extra": "not allowed"},
        "go ahead",
    ],
)
def test_invalid_resume_fails_closed(resume_value) -> None:
    executor = FakeExecutor()
    graph = build_human_approval_graph(
        executor,
        checkpointer=InMemorySaver(),
    )
    config = thread_config("approval-invalid")
    pause_graph(graph, example_proposal(), config)

    with pytest.raises(ApprovalDecisionError):
        graph.invoke(Command(resume=resume_value), config=config)

    assert executor.proposals == []
    snapshot = graph.get_state(config)
    assert snapshot.values["approval_status"] == "pending"
    assert "action_result" not in snapshot.values
    assert len(snapshot.interrupts) == 1
    assert len(snapshot.tasks) == 1
    assert snapshot.tasks[0].name == "request_approval"
    assert "ApprovalDecisionError" in (snapshot.tasks[0].error or "")


def test_different_thread_cannot_resume_paused_action() -> None:
    proposal = example_proposal()
    executor = FakeExecutor()
    graph = build_human_approval_graph(
        executor,
        checkpointer=InMemorySaver(),
    )
    original_config = thread_config("approval-original")
    other_config = thread_config("approval-other")
    pause_graph(graph, proposal, original_config)

    with pytest.raises(TypeError, match="ActionProposal"):
        graph.invoke(
            Command(resume={"decision": "approve"}),
            config=other_config,
        )

    assert executor.proposals == []
    original_state = graph.get_state(original_config)
    other_state = graph.get_state(other_config)
    assert original_state.values["action_proposal"] == proposal
    assert original_state.values["approval_status"] == "pending"
    assert original_state.next == ("request_approval",)
    assert other_state.values == {}
    assert "action_result" not in other_state.values

    graph.invoke(
        Command(resume={"decision": "approve"}),
        config=original_config,
    )
    assert executor.proposals == [proposal]
    assert graph.get_state(original_config).values[
        "approval_status"
    ] == "approved"
    assert "action_result" not in graph.get_state(other_config).values


def test_checkpointer_and_thread_configuration_are_required() -> None:
    executor = FakeExecutor()

    with pytest.raises(ValueError, match="requires a checkpointer"):
        build_human_approval_graph(
            executor,
            checkpointer=None,  # type: ignore[arg-type]
        )

    graph = build_human_approval_graph(
        executor,
        checkpointer=InMemorySaver(),
    )
    with pytest.raises(ValueError, match="thread_id"):
        graph.invoke({"action_proposal": example_proposal()})

    assert executor.proposals == []


def test_executor_exception_propagates_without_fallback() -> None:
    proposal = example_proposal()
    executor = FakeExecutor(RuntimeError("fictional executor failed"))
    graph = build_human_approval_graph(
        executor,
        checkpointer=InMemorySaver(),
    )
    config = thread_config("approval-executor-error")
    pause_graph(graph, proposal, config)

    with pytest.raises(RuntimeError, match="fictional executor failed"):
        graph.invoke(
            Command(resume={"decision": "approve"}),
            config=config,
        )

    assert executor.proposals == [proposal]
    snapshot = graph.get_state(config)
    assert snapshot.values["approval_decision"] == ApprovalDecision(
        decision="approve"
    )
    assert snapshot.values["approval_status"] == "approved"
    assert "action_result" not in snapshot.values
    assert snapshot.next == ("execute_action",)
