#!/usr/bin/env python3
"""Prove one fictional approval survives a runtime restart and executes once."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

import psycopg

from openweight_platform.api.config import DatabaseConfig
from openweight_platform.api.errors import StateConflictError
from openweight_platform.api.runtime import ApprovalCoordinator
from openweight_platform.operations.actions import AccessRequestActionService
from openweight_platform.operations.database import setup_operations_database
from openweight_platform.operations.service import OperationsLookupService
from openweight_platform.orchestration.approval import ApprovalDecision
from openweight_platform.rag.database import PostgresConfig
from openweight_platform.security.persistence import (
    PostgresApprovalSessionStore,
    open_checkpoint_handle,
    setup_security_database,
)


def _api_config(source: PostgresConfig) -> DatabaseConfig:
    return DatabaseConfig(
        database=source.database,
        user=source.user,
        password=source.password,
        host=source.host,
        port=source.port,
        sslmode=source.sslmode,
        sslrootcert=source.sslrootcert,
    )


def _coordinator(config: DatabaseConfig, handle) -> ApprovalCoordinator:
    return ApprovalCoordinator(
        OperationsLookupService(config),
        AccessRequestActionService(config),
        checkpointer=handle.saver,
        session_store=PostgresApprovalSessionStore(config),
    )


def _resume_or_conflict(
    coordinator: ApprovalCoordinator,
    approval_id: str,
) -> str:
    try:
        return coordinator.resume(
            approval_id,
            ApprovalDecision(decision="approve"),
        ).status
    except StateConflictError:
        return "conflict"


def main() -> int:
    source = PostgresConfig.from_env()
    config = _api_config(source)
    setup_operations_database(source)
    setup_security_database(config)

    first_handle = open_checkpoint_handle("postgres", config)
    try:
        first_handle.saver.setup()  # type: ignore[attr-defined]
        pending = _coordinator(config, first_handle).propose(
            "fic-req-002",
            "approved",
        )
    finally:
        first_handle.close()

    second_handle = open_checkpoint_handle("postgres", config)
    try:
        restarted = _coordinator(config, second_handle)
        outcome = restarted.resume(
            pending.approval_id,
            ApprovalDecision(decision="approve"),
        )
        try:
            restarted.resume(
                pending.approval_id,
                ApprovalDecision(decision="approve"),
            )
        except StateConflictError:
            duplicate_blocked = True
        else:
            duplicate_blocked = False
    finally:
        second_handle.close()

    proposal_handle = open_checkpoint_handle("postgres", config)
    try:
        concurrent_pending = _coordinator(config, proposal_handle).propose(
            "fic-req-006",
            "denied",
        )
    finally:
        proposal_handle.close()

    concurrent_handles = [
        open_checkpoint_handle("postgres", config),
        open_checkpoint_handle("postgres", config),
    ]
    try:
        coordinators = [
            _coordinator(config, handle) for handle in concurrent_handles
        ]
        with ThreadPoolExecutor(max_workers=2) as pool:
            concurrent_results = list(
                pool.map(
                    lambda coordinator: _resume_or_conflict(
                        coordinator,
                        concurrent_pending.approval_id,
                    ),
                    coordinators,
                )
            )
    finally:
        for handle in concurrent_handles:
            handle.close()

    rejection_handle = open_checkpoint_handle("postgres", config)
    try:
        rejected_pending = _coordinator(config, rejection_handle).propose(
            "fic-req-004",
            "approved",
        )
    finally:
        rejection_handle.close()
    rejection_resume_handle = open_checkpoint_handle("postgres", config)
    try:
        rejected = _coordinator(config, rejection_resume_handle).resume(
            rejected_pending.approval_id,
            ApprovalDecision(decision="reject"),
        )
    finally:
        rejection_resume_handle.close()

    with psycopg.connect(**config.connect_kwargs) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT approval_status FROM operations.access_requests WHERE request_id = %s",
                ("fic-req-002",),
            )
            status = cursor.fetchone()
            cursor.execute(
                "SELECT count(*) FROM security.execution_ledger WHERE effect_id = %s",
                (pending.approval_id,),
            )
            effects = cursor.fetchone()
            cursor.execute(
                "SELECT approval_status FROM operations.access_requests WHERE request_id = %s",
                ("fic-req-006",),
            )
            concurrent_status = cursor.fetchone()
            cursor.execute(
                "SELECT count(*) FROM security.execution_ledger WHERE effect_id = %s",
                (concurrent_pending.approval_id,),
            )
            concurrent_effects = cursor.fetchone()
            cursor.execute(
                "SELECT approval_status FROM operations.access_requests WHERE request_id = %s",
                ("fic-req-004",),
            )
            rejected_status = cursor.fetchone()
            cursor.execute(
                "SELECT count(*) FROM security.execution_ledger WHERE effect_id = %s",
                (rejected_pending.approval_id,),
            )
            rejected_effects = cursor.fetchone()

    if outcome.status != "approved" or status != ("approved",):
        raise RuntimeError("The restarted approval did not produce the expected effect")
    if effects != (1,) or not duplicate_blocked:
        raise RuntimeError("Duplicate execution protection did not hold")
    if sorted(concurrent_results) != ["approved", "conflict"]:
        raise RuntimeError("Concurrent resumes did not serialize safely")
    if concurrent_status != ("denied",) or concurrent_effects != (1,):
        raise RuntimeError("Concurrent approval did not produce exactly one effect")
    if (
        rejected.status != "rejected"
        or rejected_status != ("denied",)
        or rejected_effects != (0,)
    ):
        raise RuntimeError("Rejected approval produced a write")
    print(
        "Restart, replay, concurrent resume, and rejection checks passed; "
        "approved actions recorded exactly one controlled effect."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
