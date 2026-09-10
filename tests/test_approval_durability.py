from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import date

import pytest
from langgraph.checkpoint.memory import InMemorySaver

from openweight_platform.api.errors import StateConflictError
from openweight_platform.api.runtime import ApprovalCoordinator
from openweight_platform.operations.actions import (
    CLAIM_EXECUTION_QUERY,
    COMPLETE_EXECUTION_QUERY,
    READ_EXECUTION_QUERY,
    SET_ACCESS_REQUEST_STATUS_QUERY,
    AccessRequestActionService,
    AccessRequestStatusChange,
    IdempotencyConflictError,
)
from openweight_platform.operations.models import AccessRequest
from openweight_platform.orchestration.approval import ApprovalDecision
from openweight_platform.rag.database import PostgresConfig
from openweight_platform.security.persistence import (
    MemoryApprovalSessionStore,
    SECURITY_SCHEMA_STATEMENTS,
    open_checkpoint_handle,
)


@dataclass
class Reader:
    record: AccessRequest

    def lookup_access_request(self, request_id: str) -> AccessRequest | None:
        return self.record if request_id == self.record.request_id else None


class OnceWriter:
    def __init__(self) -> None:
        self.effects: dict[str, AccessRequestStatusChange] = {}
        self.calls = 0

    def set_access_request_status_once(
        self,
        effect_id: str,
        request_id: str,
        new_status: str,
    ) -> AccessRequestStatusChange:
        if effect_id not in self.effects:
            self.calls += 1
            self.effects[effect_id] = AccessRequestStatusChange(
                request_id=request_id,
                previous_status="pending",
                new_status=new_status,  # type: ignore[arg-type]
                changed=True,
            )
        return self.effects[effect_id]


def record() -> AccessRequest:
    return AccessRequest(
        request_id="req-durable",
        subject_type="employee",
        subject_id="employee-1",
        system_name="finance",
        requested_role="reader",
        approval_status="pending",
        requested_start_date=date(2026, 1, 1),
        requested_end_date=None,
    )


def test_shared_checkpoint_and_session_store_survive_coordinator_restart() -> None:
    saver = InMemorySaver()
    sessions = MemoryApprovalSessionStore()
    writer = OnceWriter()
    first = ApprovalCoordinator(
        Reader(record()),
        writer,
        checkpointer=saver,
        session_store=sessions,
    )
    pending = first.propose("req-durable", "approved")

    restarted = ApprovalCoordinator(
        Reader(record()),
        writer,
        checkpointer=saver,
        session_store=sessions,
    )
    outcome = restarted.resume(
        pending.approval_id,
        ApprovalDecision(decision="approve"),
    )

    assert outcome.status == "approved"
    assert writer.calls == 1
    with pytest.raises(StateConflictError):
        restarted.resume(
            pending.approval_id,
            ApprovalDecision(decision="approve"),
        )


def test_concurrent_duplicate_resume_produces_one_effect() -> None:
    saver = InMemorySaver()
    sessions = MemoryApprovalSessionStore()
    writer = OnceWriter()
    coordinator = ApprovalCoordinator(
        Reader(record()),
        writer,
        checkpointer=saver,
        session_store=sessions,
    )
    pending = coordinator.propose("req-durable", "denied")

    def resume() -> str:
        try:
            return coordinator.resume(
                pending.approval_id,
                ApprovalDecision(decision="approve"),
            ).status
        except StateConflictError:
            return "conflict"

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: resume(), range(2)))

    assert sorted(results) == ["approved", "conflict"]
    assert writer.calls == 1


def test_rejection_persists_without_calling_writer() -> None:
    writer = OnceWriter()
    coordinator = ApprovalCoordinator(Reader(record()), writer)
    pending = coordinator.propose("req-durable", "approved")

    outcome = coordinator.resume(
        pending.approval_id,
        ApprovalDecision(decision="reject"),
    )

    assert outcome.status == "rejected"
    assert writer.calls == 0


class ScriptedCursor:
    def __init__(self, rows: list[object]) -> None:
        self.rows = iter(rows)
        self.executions: list[tuple[str, tuple[object, ...]]] = []
        self.rowcount = 1

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def execute(self, query: str, parameters: tuple[object, ...]) -> None:
        self.executions.append((query, parameters))

    def fetchone(self):
        return next(self.rows)


class ScriptedConnection:
    def __init__(self, cursor: ScriptedCursor) -> None:
        self._cursor = cursor
        self.committed = False
        self.rolled_back = False

    def __enter__(self):
        return self

    def __exit__(self, exc_type, *_):
        self.committed = exc_type is None
        self.rolled_back = exc_type is not None
        return False

    def cursor(self) -> ScriptedCursor:
        return self._cursor


def action_service(rows: list[object]):
    cursor = ScriptedCursor(rows)
    connection = ScriptedConnection(cursor)
    config = PostgresConfig(
        database="test",
        user="test",
        password="not-a-real-secret",  # pragma: allowlist secret
    )
    return (
        AccessRequestActionService(config, connection_factory=lambda **_: connection),
        cursor,
        connection,
    )


def test_idempotency_claim_write_and_result_complete_share_transaction() -> None:
    service, cursor, connection = action_service(
        [("effect-1",), ("req-1", "pending", "approved")]
    )

    result = service.set_access_request_status_once(
        "effect-1",
        "req-1",
        "approved",
    )

    assert result.changed is True
    assert [item[0] for item in cursor.executions] == [
        CLAIM_EXECUTION_QUERY,
        SET_ACCESS_REQUEST_STATUS_QUERY,
        COMPLETE_EXECUTION_QUERY,
    ]
    assert connection.committed is True
    assert connection.rolled_back is False


def test_completed_effect_replays_result_without_second_write() -> None:
    stored = {
        "request_id": "req-1",
        "previous_status": "pending",
        "new_status": "approved",
        "changed": True,
    }
    service, cursor, _ = action_service(
        [None, ("set_access_request_status", "req-1", "approved", "completed", stored)]
    )

    result = service.set_access_request_status_once(
        "effect-1",
        "req-1",
        "approved",
    )

    assert result.changed is True
    assert [item[0] for item in cursor.executions] == [
        CLAIM_EXECUTION_QUERY,
        READ_EXECUTION_QUERY,
    ]


def test_effect_key_cannot_be_reused_for_different_action() -> None:
    service, _, connection = action_service(
        [None, ("set_access_request_status", "other", "denied", "completed", {})]
    )

    with pytest.raises(IdempotencyConflictError):
        service.set_access_request_status_once("effect-1", "req-1", "approved")

    assert connection.rolled_back is True


def test_security_schema_has_unique_pending_session_and_effect_key() -> None:
    schema = "\n".join(SECURITY_SCHEMA_STATEMENTS)

    assert "effect_id TEXT PRIMARY KEY" in schema
    assert "approval_id TEXT PRIMARY KEY" in schema
    assert "WHERE state = 'pending'" in schema
    assert "approval_sessions_one_pending_per_request" in schema


def test_explicit_postgres_checkpoint_configuration_never_falls_back() -> None:
    with pytest.raises(RuntimeError, match="requires database configuration"):
        open_checkpoint_handle("postgres", None)

    handle = open_checkpoint_handle("memory", None)
    assert isinstance(handle.saver, InMemorySaver)
    handle.close()
