from __future__ import annotations

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from openweight_platform.operations.actions import AccessRequestStatusChange
from openweight_platform.orchestration.approval import (
    ActionProposal,
    ApprovalDecisionError,
    build_human_approval_graph,
)
from openweight_platform.orchestration.operations_approval import (
    OperationalActionValidationError,
    SET_ACCESS_REQUEST_STATUS_ACTION,
    build_access_request_action_executor,
)


class FakeActionService:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def set_access_request_status_once(
        self,
        effect_id: str,
        request_id: str,
        new_status: str,
    ) -> AccessRequestStatusChange:
        assert effect_id
        self.calls.append((request_id, new_status))
        return AccessRequestStatusChange(
            request_id=request_id,
            previous_status="pending",
            new_status=new_status,
            changed=new_status != "pending",
        )


def proposal(
    *,
    action_type: str = SET_ACCESS_REQUEST_STATUS_ACTION,
    arguments=None,
) -> ActionProposal:
    return ActionProposal(
        action_type=action_type,
        summary="Set a fictional access request's approval status.",
        arguments=(
            {"request_id": "fic-req-002", "new_status": "approved"}
            if arguments is None
            else arguments
        ),
        consequence=(
            "The fictional request's approval state will be changed."
        ),
    )


def thread_config(thread_id: str) -> dict[str, dict[str, str]]:
    return {"configurable": {"thread_id": thread_id}}


def build_graph(service):
    executor = build_access_request_action_executor(service)
    return build_human_approval_graph(
        action_executor=executor,
        checkpointer=InMemorySaver(),
    )


def pause(graph, action_proposal, config):
    return graph.invoke(
        {"action_proposal": action_proposal},
        config=config,
    )


def test_initial_interrupt_performs_no_write_and_reject_stays_read_only() -> None:
    service = FakeActionService()
    graph = build_graph(service)
    config = thread_config("write-rejected")
    action_proposal = proposal()

    interrupted = pause(graph, action_proposal, config)

    assert service.calls == []
    assert interrupted["approval_status"] == "pending"
    assert interrupted["__interrupt__"][0].value["action"] == (
        action_proposal.model_dump(mode="json")
    )

    result = graph.invoke(
        Command(
            resume={
                "decision": "reject",
                "comment": "No change is authorized.",
            }
        ),
        config=config,
    )

    assert service.calls == []
    assert result["approval_status"] == "rejected"
    assert "action_result" not in result


def test_same_thread_approval_writes_once_and_persists_structured_result() -> None:
    service = FakeActionService()
    graph = build_graph(service)
    config = thread_config("write-approved")
    action_proposal = proposal()
    pause(graph, action_proposal, config)

    result = graph.invoke(
        Command(
            resume={
                "decision": "approve",
                "comment": "Approved for this fictional request.",
            }
        ),
        config=config,
    )

    expected_action_result = {
        "request_id": "fic-req-002",
        "previous_status": "pending",
        "new_status": "approved",
        "changed": True,
    }
    assert service.calls == [("fic-req-002", "approved")]
    assert result["approval_status"] == "approved"
    assert result["approval_decision"].comment == (
        "Approved for this fictional request."
    )
    assert result["action_result"] == expected_action_result
    assert graph.get_state(config).values["action_result"] == (
        expected_action_result
    )

    repeated = graph.invoke(
        Command(resume={"decision": "approve"}),
        config=config,
    )
    assert repeated == result
    assert service.calls == [("fic-req-002", "approved")]


@pytest.mark.parametrize(
    ("invalid_proposal", "message"),
    [
        (
            proposal(action_type="delete_access_request"),
            "Unsupported operational action_type",
        ),
        (
            proposal(arguments={"new_status": "approved"}),
            "arguments must contain exactly",
        ),
        (
            proposal(
                arguments={
                    "request_id": "fic-req-002",
                    "new_status": "approved",
                    "column": "subject_id",
                }
            ),
            "arguments must contain exactly",
        ),
        (
            proposal(
                arguments={
                    "request_id": "fic-req-002",
                    "new_status": "granted",
                }
            ),
            "new_status must be one of",
        ),
    ],
)
def test_invalid_approved_proposal_never_reaches_write_service(
    invalid_proposal,
    message,
) -> None:
    service = FakeActionService()
    graph = build_graph(service)
    config = thread_config(f"invalid-{invalid_proposal.action_type}-{message}")
    pause(graph, invalid_proposal, config)

    with pytest.raises(OperationalActionValidationError, match=message):
        graph.invoke(
            Command(resume={"decision": "approve"}),
            config=config,
        )

    assert service.calls == []


def test_malformed_approval_resume_never_reaches_write_service() -> None:
    service = FakeActionService()
    graph = build_graph(service)
    config = thread_config("write-malformed-resume")
    pause(graph, proposal(), config)

    with pytest.raises(ApprovalDecisionError):
        graph.invoke(
            Command(resume={"decision": "yes"}),
            config=config,
        )

    assert service.calls == []


def test_different_thread_cannot_execute_original_proposal() -> None:
    service = FakeActionService()
    graph = build_graph(service)
    original_config = thread_config("write-original")
    other_config = thread_config("write-other")
    pause(graph, proposal(), original_config)

    with pytest.raises(TypeError, match="ActionProposal"):
        graph.invoke(
            Command(resume={"decision": "approve"}),
            config=other_config,
        )

    assert service.calls == []
    assert graph.get_state(original_config).values[
        "approval_status"
    ] == "pending"
    assert graph.get_state(other_config).values == {}
