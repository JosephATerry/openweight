#!/usr/bin/env python3
"""Safely exercise the approval-gated synthetic access-request write.

The default mode stops at the real LangGraph interrupt and cannot write.
Passing ``--execute`` is an explicit operator approval gate for a local,
synthetic PostgreSQL smoke test.
"""

from __future__ import annotations

import argparse
import json
from collections.abc import Mapping, Sequence
from dataclasses import asdict, is_dataclass
from datetime import date, datetime
from typing import Any
from uuid import uuid4

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command
from pydantic import BaseModel

from openweight_platform.operations.actions import (
    ALLOWED_ACCESS_REQUEST_STATUSES,
    AccessRequestStatusChange,
    validate_access_request_status,
)
from openweight_platform.operations.models import AccessRequest
from openweight_platform.orchestration.approval import (
    ActionProposal,
    build_human_approval_graph,
)
from openweight_platform.orchestration.operations_approval import (
    SET_ACCESS_REQUEST_STATUS_ACTION,
    build_access_request_action_executor,
)


def _load_database_components() -> tuple[Any, Any, Any]:
    """Load only the PostgreSQL configuration and operations services."""
    from openweight_platform.operations.actions import (
        AccessRequestActionService,
    )
    from openweight_platform.operations.service import OperationsLookupService
    from openweight_platform.rag.database import PostgresConfig

    return PostgresConfig, OperationsLookupService, AccessRequestActionService


def _validate_fictional_request_id(request_id: object) -> str:
    if (
        not isinstance(request_id, str)
        or not request_id.strip()
        or request_id != request_id.strip()
    ):
        raise ValueError("request_id must be a non-empty exact string")
    if not request_id.startswith("fic-req-"):
        raise ValueError("request_id must use the fictional 'fic-req-' namespace")
    return request_id


def _argparse_request_id(value: str) -> str:
    try:
        return _validate_fictional_request_id(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError(str(error)) from error


def _require_synthetic_record(
    record: AccessRequest | None,
    request_id: str,
) -> AccessRequest:
    if record is None:
        raise LookupError(f"Fictional access request {request_id!r} was not found")
    if (
        record.request_id != request_id
        or not record.subject_id.startswith("fic-")
        or not record.system_name.startswith("Fictional ")
    ):
        raise ValueError(
            f"Access request {request_id!r} is not clearly synthetic"
        )

    try:
        validate_access_request_status(record.approval_status)
    except ValueError as error:
        raise ValueError(
            f"Access request {request_id!r} has an unsupported current status"
        ) from error
    return record


def _build_proposal(
    record: AccessRequest,
    new_status: str,
) -> ActionProposal:
    return ActionProposal(
        action_type=SET_ACCESS_REQUEST_STATUS_ACTION,
        summary=(
            f"Set fictional access request {record.request_id} "
            f"from {record.approval_status} to {new_status}."
        ),
        arguments={
            "request_id": record.request_id,
            "new_status": new_status,
        },
        consequence=(
            "The approval status of this fictional access request will be "
            "committed to local PostgreSQL if explicitly approved."
        ),
    )


def run_approval_write_smoke(
    request_id: str,
    new_status: str,
    *,
    execute: bool = False,
    thread_id: str | None = None,
) -> dict[str, object]:
    """Pause for review by default; execute only behind the explicit gate."""
    validated_request_id = _validate_fictional_request_id(request_id)
    validated_new_status = validate_access_request_status(new_status)
    config_type, lookup_type, action_type = _load_database_components()

    database_config = config_type.from_env()
    lookup_service = lookup_type(database_config)
    before = _require_synthetic_record(
        lookup_service.lookup_access_request(validated_request_id),
        validated_request_id,
    )

    proposal = _build_proposal(before, validated_new_status)
    action_service = action_type(database_config)
    executor = build_access_request_action_executor(action_service)
    graph = build_human_approval_graph(
        action_executor=executor,
        checkpointer=InMemorySaver(),
    )
    resolved_thread_id = thread_id or f"approval-write-smoke-{uuid4().hex}"
    graph_config = {"configurable": {"thread_id": resolved_thread_id}}

    interrupted = graph.invoke(
        {"action_proposal": proposal},
        config=graph_config,
    )
    interrupts = interrupted.get("__interrupt__")
    if not isinstance(interrupts, list) or len(interrupts) != 1:
        raise RuntimeError("Approval graph did not return exactly one interrupt")

    paused_snapshot = graph.get_state(graph_config)
    after_interrupt = _require_synthetic_record(
        lookup_service.lookup_access_request(validated_request_id),
        validated_request_id,
    )
    if after_interrupt != before:
        raise RuntimeError("Database record changed before human approval")

    document: dict[str, object] = {
        "mode": "execute" if execute else "dry_run",
        "thread_id": resolved_thread_id,
        "request_before": _json_value(before),
        "proposed_new_status": validated_new_status,
        "action_proposal": proposal.model_dump(mode="json"),
        "interrupt_payload": _json_value(interrupts[0].value),
        "pre_approval_database_unchanged": True,
        "paused_approval_status": paused_snapshot.values.get(
            "approval_status"
        ),
        "write_performed": False,
    }
    if not execute:
        document["message"] = (
            "No write was performed. Re-run with --execute only after "
            "reviewing this approval payload."
        )
        return document

    completed = graph.invoke(
        Command(
            resume={
                "decision": "approve",
                "comment": "Explicit --execute smoke-test approval.",
            }
        ),
        config=graph_config,
    )
    after = _require_synthetic_record(
        lookup_service.lookup_access_request(validated_request_id),
        validated_request_id,
    )
    completed_snapshot = graph.get_state(graph_config)
    action_result = completed.get("action_result")
    _validate_completed_result(
        action_result,
        before=before,
        after=after,
        new_status=validated_new_status,
    )
    if completed_snapshot.next:
        raise RuntimeError("Approval graph did not reach a completed state")

    document.update(
        {
            "approval_decision": _json_value(
                completed.get("approval_decision")
            ),
            "approval_status": completed.get("approval_status"),
            "action_result": _json_value(action_result),
            "request_after": _json_value(after),
            "database_status_changed": (
                before.approval_status != after.approval_status
            ),
            "graph_completed": True,
            "write_performed": True,
        }
    )
    return document


def _validate_completed_result(
    value: object,
    *,
    before: AccessRequest,
    after: AccessRequest,
    new_status: str,
) -> None:
    expected = asdict(
        AccessRequestStatusChange(
            request_id=before.request_id,
            previous_status=before.approval_status,
            new_status=validate_access_request_status(new_status),
            changed=before.approval_status != new_status,
        )
    )
    if value != expected:
        raise RuntimeError("Action result does not match the proposed transition")
    if after.approval_status != new_status:
        raise RuntimeError("Independent database read does not match action result")


def _json_value(value: object) -> object:
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if is_dataclass(value) and not isinstance(value, type):
        return _json_value(asdict(value))
    if isinstance(value, Mapping):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        return [_json_value(item) for item in value]
    return str(value)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Pause a synthetic access-request status action for approval; "
            "write only with explicit --execute."
        )
    )
    parser.add_argument(
        "--request-id",
        required=True,
        type=_argparse_request_id,
        help="Exact fictional request ID in the fic-req- namespace.",
    )
    parser.add_argument(
        "--new-status",
        required=True,
        choices=tuple(sorted(ALLOWED_ACCESS_REQUEST_STATUSES)),
        help="Proposed access-request status.",
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help=(
            "Explicitly approve and commit the proposed local synthetic write; "
            "omit for the safe interrupt-only dry run."
        ),
    )
    return parser


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    return build_parser().parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    result = run_approval_write_smoke(
        args.request_id,
        args.new_status,
        execute=args.execute,
    )
    print(
        json.dumps(
            result,
            indent=2,
            sort_keys=True,
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
