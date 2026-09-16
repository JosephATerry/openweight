from __future__ import annotations

import sys
from types import SimpleNamespace
from typing import Any

import pytest

from scripts import migrate_database


class ValidationCursor:
    def __init__(self) -> None:
        self.statement = ""
        self.parameters: tuple[Any, ...] = ()

    def __enter__(self) -> ValidationCursor:
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def execute(
        self,
        statement: str,
        parameters: tuple[Any, ...] | None = None,
    ) -> None:
        self.statement = " ".join(statement.split())
        self.parameters = parameters or ()

    def fetchone(self) -> tuple[Any, ...]:
        statement = self.statement
        if "SELECT extversion" in statement:
            return ("0.8.1",)
        if "SELECT vector_dims" in statement:
            return (migrate_database.EMBEDDING_DIMENSION,)
        if "SELECT format_type" in statement:
            return (f"vector({migrate_database.EMBEDDING_DIMENSION})",)
        if "FROM openweight_admin.schema_migrations" in statement:
            return (1,)
        if "contype = 'u'" in statement:
            return (1, 2, 2)
        if "approval_sessions_one_pending_per_request" in statement:
            return (True,)
        if "FROM pg_roles" in statement:
            return (True, False, False, False, False, False)
        if "FROM pg_auth_members" in statement:
            return (False,)
        if "JOIN pg_roles role" in statement:
            return (False, False)
        if "has_database_privilege" in statement:
            return (True, False, False, False)
        if "has_schema_privilege" in statement:
            return (True, False)
        if "has_column_privilege" in statement:
            return (True, True, False)
        if "has_table_privilege" in statement:
            if len(self.parameters) == 2:
                table = "operations.access_requests"
                privilege = str(self.parameters[1])
            else:
                table = str(self.parameters[1])
                privilege = str(self.parameters[2])
            read_only = {
                "operations.employees",
                "operations.contractors",
                "public.checkpoint_migrations",
            }
            if table == "operations.access_requests":
                return (privilege == "SELECT",)
            if table in read_only:
                return (privilege == "SELECT",)
            return (privilege in {"SELECT", "INSERT", "UPDATE", "DELETE"},)
        if "SELECT count(*) FROM operations." in statement:
            table = statement.rsplit(".", maxsplit=1)[1]
            return (migrate_database.EXPECTED_DEMO_COUNTS[table],)
        raise AssertionError(f"Unexpected validation query: {statement}")

    def fetchall(self) -> list[tuple[Any, ...]]:
        if "FROM pg_namespace" in self.statement:
            return [(name,) for name in migrate_database.EXPECTED_SCHEMAS]
        if "to_regclass(expected_name)::text" in self.statement:
            return [(name, name) for name in migrate_database.EXPECTED_TABLES]
        if "FROM pg_constraint" in self.statement:
            return [
                (name, True) for name in migrate_database.EXPECTED_PRIMARY_KEY_TABLES
            ]
        if "FROM pg_indexes" in self.statement:
            return [("CREATE UNIQUE INDEX policy_chunks_pkey USING btree (id)",)]
        raise AssertionError(f"Unexpected validation query: {self.statement}")


class ValidationConnection:
    def __init__(self, cursor: ValidationCursor) -> None:
        self._cursor = cursor

    def __enter__(self) -> ValidationConnection:
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def cursor(self) -> ValidationCursor:
        return self._cursor


def test_unrecorded_ledger_only_state_resumes_role_schema_and_version(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []
    config = object()

    monkeypatch.setattr(migrate_database, "_is_applied", lambda _config: False)
    monkeypatch.setattr(
        migrate_database,
        "ensure_runtime_database_role",
        lambda _config, _role, _password: calls.append("role"),
    )
    monkeypatch.setattr(
        migrate_database, "_apply_schema", lambda _config: calls.append("schema")
    )
    monkeypatch.setattr(
        migrate_database, "_record_migration", lambda _config: calls.append("record")
    )

    assert migrate_database._apply_current_migration(
        config,
        "openweight_app",
        "not-a-real-runtime-secret",  # pragma: allowlist secret
    ) is True
    assert calls == ["role", "schema", "record"]


def test_current_migration_is_distinguishable_and_idempotent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = object()
    monkeypatch.setattr(migrate_database, "_is_applied", lambda _config: True)
    monkeypatch.setattr(
        migrate_database,
        "ensure_runtime_database_role",
        lambda *_args: pytest.fail("current migration must not rotate credentials"),
    )
    monkeypatch.setattr(
        migrate_database,
        "_apply_schema",
        lambda _config: pytest.fail("current migration must not run schema operations"),
    )
    monkeypatch.setattr(
        migrate_database,
        "_record_migration",
        lambda _config: pytest.fail("current migration must not be recorded again"),
    )

    assert migrate_database._apply_current_migration(
        config,
        "openweight_app",
        "not-a-real-runtime-secret",  # pragma: allowlist secret
    ) is False


@pytest.mark.parametrize(
    ("applied", "expected_status"),
    ((True, "applied"), (False, "already current")),
)
def test_main_reports_safe_migration_and_validation_status(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    applied: bool,
    expected_status: str,
) -> None:
    monkeypatch.setattr(sys, "argv", ["migrate_database.py", "--seed-demo-data"])
    monkeypatch.setattr(
        migrate_database,
        "run",
        lambda **_kwargs: migrate_database.MigrationResult(
            applied=applied,
            vector_version="0.8.1",
        ),
    )

    assert migrate_database.main() == 0
    output = capsys.readouterr()
    assert f"Database migration 1 {expected_status}." in output.out
    assert "Database validation successful." in output.out
    assert output.err == ""


def test_main_redacts_driver_exception_details(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    marker = "credential-material-must-not-appear"
    monkeypatch.setattr(sys, "argv", ["migrate_database.py"])

    def fail(**_kwargs: object) -> migrate_database.MigrationResult:
        raise RuntimeError(f"postgresql://user:{marker}@private-host/database")

    monkeypatch.setattr(migrate_database, "run", fail)

    assert migrate_database.main() == 1
    output = capsys.readouterr()
    assert output.out == ""
    assert output.err == "Database migration or validation failed.\n"
    assert marker not in output.err


def test_postcondition_validation_exercises_complete_security_contract(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cursor = ValidationCursor()
    connection = ValidationConnection(cursor)
    monkeypatch.setattr(
        migrate_database.psycopg,
        "connect",
        lambda **_kwargs: connection,
    )

    result = migrate_database._validate_postconditions(
        SimpleNamespace(connect_kwargs={}),  # type: ignore[arg-type]
        "openweight_app",
        expect_demo_data=True,
    )

    assert result == "0.8.1"
