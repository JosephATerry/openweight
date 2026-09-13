"""Durable approval-session and execution-ledger persistence."""

from __future__ import annotations

import os
import threading
from collections.abc import Iterator
from contextlib import AbstractContextManager, contextmanager
from dataclasses import dataclass
from typing import Any, Literal, Protocol

import psycopg
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from psycopg import sql
from psycopg.errors import UniqueViolation
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from pydantic import JsonValue

from openweight_platform.api.config import DatabaseConfig
from openweight_platform.orchestration.approval import ActionProposal


ApprovalSessionState = Literal["pending", "approved", "rejected"]

SECURITY_SCHEMA_STATEMENTS = (
    "CREATE SCHEMA IF NOT EXISTS security",
    """
    CREATE TABLE IF NOT EXISTS security.approval_sessions (
        approval_id TEXT PRIMARY KEY,
        access_request_id TEXT NOT NULL,
        proposed_status TEXT NOT NULL,
        proposal JSONB NOT NULL,
        state TEXT NOT NULL DEFAULT 'pending'
            CHECK (state IN ('pending', 'approved', 'rejected')),
        outcome JSONB,
        created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
        completed_at TIMESTAMPTZ,
        CHECK (
            (state = 'pending' AND outcome IS NULL AND completed_at IS NULL)
            OR
            (state IN ('approved', 'rejected') AND completed_at IS NOT NULL)
        )
    )
    """,
    """
    CREATE UNIQUE INDEX IF NOT EXISTS approval_sessions_one_pending_per_request
    ON security.approval_sessions (access_request_id)
    WHERE state = 'pending'
    """,
    """
    CREATE TABLE IF NOT EXISTS security.execution_ledger (
        effect_id TEXT PRIMARY KEY,
        action_type TEXT NOT NULL,
        access_request_id TEXT NOT NULL,
        requested_status TEXT NOT NULL,
        state TEXT NOT NULL CHECK (state IN ('claimed', 'completed')),
        result JSONB,
        created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
        completed_at TIMESTAMPTZ,
        CHECK (
            (state = 'claimed' AND result IS NULL AND completed_at IS NULL)
            OR
            (state = 'completed' AND result IS NOT NULL AND completed_at IS NOT NULL)
        )
    )
    """,
)


class ApprovalSessionNotFoundError(LookupError):
    """Raised when a durable approval identifier does not exist."""


class ApprovalSessionConflictError(RuntimeError):
    """Raised for duplicate or already-completed approval state."""


@dataclass(frozen=True)
class ApprovalSessionRecord:
    approval_id: str
    access_request_id: str
    proposed_status: str
    proposal: ActionProposal
    state: ApprovalSessionState = "pending"
    outcome: JsonValue | None = None


class LockedApprovalSession(Protocol):
    @property
    def record(self) -> ApprovalSessionRecord: ...

    def complete(
        self,
        state: Literal["approved", "rejected"],
        outcome: JsonValue | None,
    ) -> None: ...


class ApprovalSessionStore(Protocol):
    def create(self, record: ApprovalSessionRecord) -> None: ...

    def delete_pending(self, approval_id: str) -> None: ...

    def locked(self, approval_id: str) -> AbstractContextManager[LockedApprovalSession]: ...


@dataclass
class _MutableMemorySession:
    record: ApprovalSessionRecord


class _MemoryLockedSession:
    def __init__(self, session: _MutableMemorySession) -> None:
        self._session = session

    @property
    def record(self) -> ApprovalSessionRecord:
        return self._session.record

    def complete(
        self,
        state: Literal["approved", "rejected"],
        outcome: JsonValue | None,
    ) -> None:
        self._session.record = ApprovalSessionRecord(
            approval_id=self.record.approval_id,
            access_request_id=self.record.access_request_id,
            proposed_status=self.record.proposed_status,
            proposal=self.record.proposal,
            state=state,
            outcome=outcome,
        )


class MemoryApprovalSessionStore:
    """Process-local store retained only for local development and tests."""

    def __init__(self) -> None:
        self._sessions: dict[str, _MutableMemorySession] = {}
        self._lock = threading.RLock()

    def create(self, record: ApprovalSessionRecord) -> None:
        with self._lock:
            if record.approval_id in self._sessions or any(
                item.record.access_request_id == record.access_request_id
                and item.record.state == "pending"
                for item in self._sessions.values()
            ):
                raise ApprovalSessionConflictError
            self._sessions[record.approval_id] = _MutableMemorySession(record)

    def delete_pending(self, approval_id: str) -> None:
        with self._lock:
            current = self._sessions.get(approval_id)
            if current is not None and current.record.state == "pending":
                self._sessions.pop(approval_id, None)

    @contextmanager
    def locked(self, approval_id: str) -> Iterator[_MemoryLockedSession]:
        with self._lock:
            session = self._sessions.get(approval_id)
            if session is None:
                raise ApprovalSessionNotFoundError
            if session.record.state != "pending":
                raise ApprovalSessionConflictError
            yield _MemoryLockedSession(session)


class _PostgresLockedSession:
    def __init__(self, cursor: Any, record: ApprovalSessionRecord) -> None:
        self._cursor = cursor
        self._record = record
        self._completed = False

    @property
    def record(self) -> ApprovalSessionRecord:
        return self._record

    def complete(
        self,
        state: Literal["approved", "rejected"],
        outcome: JsonValue | None,
    ) -> None:
        if self._completed:
            raise ApprovalSessionConflictError
        self._cursor.execute(
            """
            UPDATE security.approval_sessions
            SET state = %s, outcome = %s, completed_at = CURRENT_TIMESTAMP
            WHERE approval_id = %s AND state = 'pending'
            """,
            (state, Jsonb(outcome) if outcome is not None else None, self._record.approval_id),
        )
        if self._cursor.rowcount != 1:
            raise ApprovalSessionConflictError
        self._completed = True


class PostgresApprovalSessionStore:
    """Cross-process approval coordination using row locks and constraints."""

    def __init__(
        self,
        config: DatabaseConfig,
        *,
        connection_factory: Any | None = None,
    ) -> None:
        self._config = config
        self._connection_factory = connection_factory or psycopg.connect

    def create(self, record: ApprovalSessionRecord) -> None:
        try:
            with self._connection_factory(**self._config.connect_kwargs) as connection:
                with connection.cursor() as cursor:
                    cursor.execute(
                        """
                        INSERT INTO security.approval_sessions (
                            approval_id, access_request_id, proposed_status,
                            proposal, state
                        ) VALUES (%s, %s, %s, %s, 'pending')
                        """,
                        (
                            record.approval_id,
                            record.access_request_id,
                            record.proposed_status,
                            Jsonb(record.proposal.model_dump(mode="json")),
                        ),
                    )
        except UniqueViolation as error:
            raise ApprovalSessionConflictError from error

    def delete_pending(self, approval_id: str) -> None:
        with self._connection_factory(**self._config.connect_kwargs) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    DELETE FROM security.approval_sessions
                    WHERE approval_id = %s AND state = 'pending'
                    """,
                    (approval_id,),
                )

    @contextmanager
    def locked(self, approval_id: str) -> Iterator[_PostgresLockedSession]:
        with self._connection_factory(**self._config.connect_kwargs) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT approval_id, access_request_id, proposed_status,
                           proposal, state, outcome
                    FROM security.approval_sessions
                    WHERE approval_id = %s
                    FOR UPDATE
                    """,
                    (approval_id,),
                )
                row = cursor.fetchone()
                if row is None:
                    raise ApprovalSessionNotFoundError
                if row[4] != "pending":
                    raise ApprovalSessionConflictError
                record = ApprovalSessionRecord(
                    approval_id=row[0],
                    access_request_id=row[1],
                    proposed_status=row[2],
                    proposal=ActionProposal.model_validate(row[3], strict=True),
                    state=row[4],
                    outcome=row[5],
                )
                yield _PostgresLockedSession(cursor, record)


class CheckpointHandle:
    """Own a saver and any context that must stay open for its lifetime."""

    def __init__(
        self,
        saver: BaseCheckpointSaver,
        context: AbstractContextManager[Any] | None = None,
    ) -> None:
        self.saver = saver
        self._context = context

    def close(self) -> None:
        if self._context is not None:
            context = self._context
            self._context = None
            context.__exit__(None, None, None)


def open_checkpoint_handle(
    backend: Literal["memory", "postgres"],
    config: DatabaseConfig | None,
) -> CheckpointHandle:
    """Open the configured saver; never fall back after PostgreSQL failure."""
    if backend == "memory":
        return CheckpointHandle(InMemorySaver())
    if config is None:
        raise RuntimeError("PostgreSQL checkpointing requires database configuration")

    from langgraph.checkpoint.postgres import PostgresSaver

    os.environ.setdefault("LANGGRAPH_STRICT_MSGPACK", "true")
    connection = psycopg.connect(
        config.conninfo,
        autocommit=True,
        prepare_threshold=0,
        row_factory=dict_row,
    )
    serializer = JsonPlusSerializer(
        allowed_msgpack_modules=[
            ("openweight_platform.orchestration.approval", "ActionProposal"),
            ("openweight_platform.orchestration.approval", "ApprovalDecision"),
        ]
    )
    return CheckpointHandle(PostgresSaver(connection, serde=serializer), connection)


def setup_security_database(config: DatabaseConfig) -> None:
    """Create D14-owned tables; checkpoint migrations are a separate call."""
    with psycopg.connect(**config.connect_kwargs) as connection:
        with connection.cursor() as cursor:
            for statement in SECURITY_SCHEMA_STATEMENTS:
                cursor.execute(statement)


def grant_runtime_database_role(
    config: DatabaseConfig,
    runtime_role: str,
) -> None:
    """Grant an existing login role only the application's runtime privileges."""
    if not runtime_role or len(runtime_role) > 63:
        raise ValueError("runtime_role must be a valid non-empty identifier")
    role = sql.Identifier(runtime_role)
    database = sql.Identifier(config.database)
    statements = (
        sql.SQL("GRANT CONNECT ON DATABASE {} TO {}").format(database, role),
        sql.SQL("GRANT USAGE ON SCHEMA operations, security, public TO {}").format(role),
        sql.SQL(
            "GRANT SELECT ON operations.employees, operations.contractors, "
            "operations.access_requests TO {}"
        ).format(role),
        sql.SQL(
            "GRANT UPDATE (approval_status) ON operations.access_requests TO {}"
        ).format(role),
        sql.SQL(
            "GRANT SELECT, INSERT, UPDATE, DELETE ON "
            "security.approval_sessions, security.execution_ledger TO {}"
        ).format(role),
        sql.SQL(
            "GRANT SELECT, INSERT, UPDATE, DELETE ON "
            "public.checkpoints, public.checkpoint_blobs, "
            "public.checkpoint_writes TO {}"
        ).format(role),
        sql.SQL("GRANT SELECT ON public.checkpoint_migrations TO {}").format(role),
    )
    with psycopg.connect(**config.connect_kwargs) as connection:
        with connection.cursor() as cursor:
            for statement in statements:
                cursor.execute(statement)
            cursor.execute(
                """
                SELECT
                    to_regclass('public.langchain_pg_collection') IS NOT NULL,
                    to_regclass('public.langchain_pg_embedding') IS NOT NULL
                """
            )
            if cursor.fetchone() == (True, True):
                cursor.execute(
                    sql.SQL(
                        "GRANT SELECT ON public.langchain_pg_collection, "
                        "public.langchain_pg_embedding TO {}"
                    ).format(role)
                )
            cursor.execute(
                "SELECT to_regclass('public.policy_chunks') IS NOT NULL"
            )
            if cursor.fetchone() == (True,):
                cursor.execute(
                    sql.SQL("GRANT SELECT ON public.policy_chunks TO {}").format(role)
                )


def ensure_runtime_database_role(
    config: DatabaseConfig,
    runtime_role: str,
    runtime_password: str,
) -> None:
    """Create or rotate the narrow login role during an explicit migration."""

    if not runtime_role or len(runtime_role) > 63:
        raise ValueError("runtime_role must be a valid non-empty identifier")
    if not runtime_password:
        raise ValueError("runtime_password must not be empty")
    role = sql.Identifier(runtime_role)
    with psycopg.connect(**config.connect_kwargs) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = %s)",
                (runtime_role,),
            )
            row = cursor.fetchone()
            if row != (True,):
                cursor.execute(sql.SQL("CREATE ROLE {} LOGIN").format(role))
            cursor.execute(
                sql.SQL("ALTER ROLE {} LOGIN PASSWORD %s").format(role),
                (runtime_password,),
            )


__all__ = [
    "ApprovalSessionConflictError",
    "ApprovalSessionNotFoundError",
    "ApprovalSessionRecord",
    "ApprovalSessionStore",
    "CheckpointHandle",
    "MemoryApprovalSessionStore",
    "PostgresApprovalSessionStore",
    "SECURITY_SCHEMA_STATEMENTS",
    "open_checkpoint_handle",
    "ensure_runtime_database_role",
    "grant_runtime_database_role",
    "setup_security_database",
]
